from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
import timeit
from multiprocessing.managers import SyncManager
from typing import Type, TYPE_CHECKING, Tuple

import math
from ConfigSpace.configuration_space import ConfigurationSpace
from sklearn.pipeline import Pipeline

from dswizard.core.base_structure_generator import BaseStructureGenerator
from dswizard.core.config_cache import ConfigCache
from dswizard.core.dispatcher import Dispatcher
from dswizard.core.logger import JsonResultLogger
from dswizard.core.model import Job, Dataset
from dswizard.core.runhistory import RunHistory
from dswizard.optimizers.bandit_learners import HyperbandLearner
from dswizard.optimizers.config_generators import RandomSampling
from dswizard.optimizers.structure_generators.mcts import MCTS
from dswizard.workers import SklearnWorker

if TYPE_CHECKING:
    from dswizard.core.base_bandit_learner import BanditLearner
    from dswizard.core.base_config_generator import BaseConfigGenerator
    from dswizard.core.worker import Worker


class Master:
    def __init__(self,
                 ds: Dataset,
                 working_directory: str = '.',
                 logger: logging.Logger = None,
                 result_logger: JsonResultLogger = None,

                 wallclock_limit: int = 60,
                 cutoff: int = None,
                 pre_sample: bool = True,

                 n_workers: int = 1,
                 worker_class: Type[Worker] = SklearnWorker,

                 config_generator_class: Type[BaseConfigGenerator] = RandomSampling,
                 config_generator_kwargs: dict = None,

                 structure_generator_class: Type[BaseStructureGenerator] = MCTS,
                 structure_generator_kwargs: dict = None,

                 bandit_learner_class: Type[BanditLearner] = HyperbandLearner,
                 bandit_learner_kwargs: dict = None
                 ):
        """
        The Master class is responsible for the book keeping and to decide what to run next. Optimizers are
        instantiations of Master, that handle the important steps of deciding what configurations to run on what
        budget when.
        :param working_directory: The top level working directory accessible to all compute nodes(shared filesystem).
        :param logger: the logger to output some (more or less meaningful) information
        :param result_logger: a result logger that writes live results to disk
        """

        if bandit_learner_kwargs is None:
            bandit_learner_kwargs = {}
        if config_generator_kwargs is None:
            config_generator_kwargs = {}
        if structure_generator_kwargs is None:
            structure_generator_kwargs = {}

        self.working_directory = working_directory
        os.makedirs(self.working_directory, exist_ok=True)
        if 'working_directory' not in config_generator_kwargs:
            config_generator_kwargs['working_directory'] = self.working_directory

        if logger is None:
            self.logger = logging.getLogger('Master')
        else:
            self.logger = logger

        if result_logger is None:
            result_logger = JsonResultLogger(self.working_directory, overwrite=True)
        self.result_logger = result_logger
        self.jobs = []
        self.meta_data = {}

        self.ds = ds
        self.ds.cutoff = cutoff
        self.wallclock_limit = wallclock_limit
        self.cutoff = cutoff
        self.pre_sample = pre_sample

        # condition to synchronize the job_callback and the queue
        self.thread_cond = threading.Condition()

        SyncManager.register('ConfigCache', ConfigCache)
        mgr = multiprocessing.Manager()
        # noinspection PyUnresolvedReferences
        self.cfg_cache: ConfigCache = mgr.ConfigCache(clazz=config_generator_class,
                                                      init_kwargs=config_generator_kwargs)

        self.workers = []
        if 'worker' not in structure_generator_kwargs:
            self.workers.append(worker_class(wid='structure', metric=ds.metric, workdir=self.working_directory))
            structure_generator_kwargs['worker'] = self.workers[-1]
        self.structure_generator = structure_generator_class(cfg_cache=self.cfg_cache,
                                                             cutoff=self.cutoff,
                                                             workdir=self.working_directory,
                                                             **structure_generator_kwargs)

        if n_workers < 1:
            raise ValueError('Expected at least 1 worker, given {}'.format(n_workers))
        for i in range(n_workers):
            self.workers.append(worker_class(wid=str(i), cfg_cache=self.cfg_cache, metric=ds.metric,
                                             workdir=self.working_directory))

        self.dispatcher = Dispatcher(self.workers[1:])
        self.dispatcher_thread = threading.Thread(target=self.dispatcher.run)
        self.dispatcher_thread.start()

        self.bandit_learner: BanditLearner = bandit_learner_class(self.dispatcher, **bandit_learner_kwargs)

    def shutdown(self) -> None:
        self.logger.info('shutdown initiated')
        # Sleep one second to guarantee dispatcher start, if startup procedure fails
        time.sleep(1)
        self.dispatcher.shutdown()
        self.dispatcher_thread.join()
        self.structure_generator.shutdown()

    def optimize(self) -> Tuple[Pipeline, RunHistory]:
        """
        run optimization
        :return:
        """

        start = time.time()
        self.meta_data['start'] = start
        self.logger.info('starting run at {}. Configuration:\n'
                         '\twallclock_limit: {}\n'
                         '\tcutoff: {}\n'
                         '\tpre_sample: {}'.format(time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(start)),
                                                   self.wallclock_limit, self.cutoff, self.pre_sample))
        relative_start = timeit.default_timer()
        for worker in self.workers:
            worker.start_time = relative_start

        def _optimize() -> bool:
            for candidate, iteration in self.bandit_learner.next_candidate():
                if candidate.is_proxy():
                    candidate = self.structure_generator.fill_candidate(candidate, self.ds)
                    self.result_logger.new_structure(candidate)

                # Optimize hyperparameters
                n_configs = int(candidate.budget)
                cid_offset = len(candidate.results)
                for i in range(cid_offset, cid_offset + n_configs):
                    config_id = candidate.cid.with_config(i)
                    if self.pre_sample:
                        config, cfg_key = self.cfg_cache.sample_configuration(
                            configspace=candidate.pipeline.configuration_space,
                            mf=self.ds.meta_features)
                        job = Job(self.ds, config_id, candidate, self.cutoff, config, [cfg_key])
                    else:
                        job = Job(self.ds, config_id, candidate, self.cutoff, None, candidate.cfg_keys)
                    job.callback = self._result_callback

                    if time.time() > start + self.wallclock_limit:
                        self.logger.info("Timeout reached. Stopping optimization")
                        self.dispatcher.finish_work()
                        return True

                    self.dispatcher.submit_job(job)
            return False

        # while time_limit is not exhausted:
        #   structure, budget = structure_generator.get_next_structure()
        #   configspace = structure.configspace
        #
        #   incumbent, loss = bandit_learners.optimize(configspace, structure)
        #   Update score of selected structure with loss

        # Main hyperparamter optimization logic
        timeout = False
        repetition = 0
        offset = 0
        try:
            while not timeout:
                self.logger.info('Starting repetition {}'.format(repetition))
                self.bandit_learner.reset(offset)
                timeout = _optimize()
                repetition += 1
                offset += sum([len(it.data) for it in self.bandit_learner.iterations])
        except KeyboardInterrupt:
            self.logger.info('Aborting optimization due to user interrupt')

        end = time.time()
        self.meta_data['end'] = end
        self.logger.info('Finished run after {} seconds'.format(math.ceil(end - start)))

        iterations = self.result_logger.load()
        rh = RunHistory(iterations, {**self.meta_data, **self.bandit_learner.meta_data})
        pipeline, _ = rh.get_incumbent()
        return pipeline, rh

    def _result_callback(self, job: Job) -> None:
        """
        method to be called when a job has finished

        this will do some book keeping and call the user defined new_result_callback if one was specified
        :param job: Finished Job
        :return:
        """
        with self.thread_cond:
            try:
                if job.config is None:
                    self.logger.error(
                        'Encountered job without a configuration: {}. Using empty config as fallback'.format(job.cid))
                    job.config = ConfigurationSpace().get_default_configuration()

                if self.result_logger is not None:
                    self.result_logger.log_evaluated_config(job.cid, job.result)

                job.callback = None
                job.cs.add_result(job.result)
                self.cfg_cache.register_result(job)
                self.bandit_learner.register_result(job)
                self.structure_generator.register_result(job.cs, job.result)
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                self.logger.fatal('Encountered unhandled exception {}. This should never happen!'.format(ex),
                                  exc_info=True)
