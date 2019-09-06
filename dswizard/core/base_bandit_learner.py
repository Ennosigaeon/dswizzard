import abc
import logging
from typing import List, Callable, Optional, Tuple

from ConfigSpace import Configuration

from dswizard.core.base_iteration import BaseIteration
from dswizard.core.base_structure_generator import BaseStructureGenerator
from dswizard.core.config_generator_cache import ConfigGeneratorCache
from dswizard.core.model import CandidateStructure, CandidateId


class BanditLearner(abc.ABC):

    def __init__(self,
                 structure_generator: BaseStructureGenerator = None,
                 logger: logging.Logger = None):
        self.structure_generator = structure_generator

        if logger is None:
            self.logger = logging.getLogger('Racing')
        else:
            self.logger = logger

        self.iterations: List[BaseIteration] = []
        self.config = {}
        self.max_iterations = 0

    @abc.abstractmethod
    def _get_next_iteration(self, iteration: int, iteration_kwargs: dict) -> BaseIteration:
        """
        instantiates the next iteration

        Overwrite this to change the iterations for different optimizers
        :param iteration: the index of the iteration to be instantiated
        :param iteration_kwargs: additional kwargs for the iteration class. Defaults to empty dictionary
        :return: a valid HB iteration object
        """
        pass

    def optimize(self, starter: Callable[[CandidateId, CandidateStructure, Optional[Configuration]], None],
                 iteration_kwargs: dict) -> None:
        """
        Optimize all hyperparameters
        :param starter:
        :param iteration_kwargs:
        :return:
        """
        # noinspection PyTypeChecker
        for candidate, iteration in self._get_next_structure(iteration_kwargs):
            cg = ConfigGeneratorCache.instance().get(candidate.pipeline)
            cg.optimize(starter, candidate)

            self.iterations[iteration].register_result(candidate)
            self.structure_generator.new_result(candidate)

    def _get_next_structure(self, iteration_kwargs: dict = None) -> List[Tuple[CandidateStructure, int]]:
        n_iterations = self.max_iterations
        while True:
            next_candidate = None
            # find a new run to schedule
            for i in filter(lambda idx: not self.iterations[idx].is_finished, range(len(self.iterations))):
                next_candidate = self.iterations[i].get_next_candidate()
                if next_candidate is not None:
                    break

            if next_candidate is not None:
                # noinspection PyUnboundLocalVariable
                yield next_candidate, i
            else:
                if n_iterations > 0:  # we might be able to start the next iteration
                    iteration = len(self.iterations)
                    self.logger.info('Starting iteration {}'.format(iteration))
                    self.iterations.append(self._get_next_iteration(iteration, iteration_kwargs))
                    n_iterations -= 1
                else:
                    # Done
                    break