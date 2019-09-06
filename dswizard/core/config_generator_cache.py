import logging
from typing import Dict, Type

import Pyro4
from ConfigSpace import ConfigurationSpace

from dswizard.components.pipeline import FlexiblePipeline
from dswizard.core.base_config_generator import BaseConfigGenerator
from dswizard.core.model import Job
from dswizard.util.singleton import Singleton


@Singleton
class ConfigGeneratorCache:

    def __init__(self,
                 clazz: Type[BaseConfigGenerator],
                 init_kwargs: dict,
                 nameserver: str = None,
                 nameserver_port: int = None,
                 host: str = None,
                 run_id: str = '0',
                 logger: logging.Logger = None
                 ):
        self.clazz = clazz
        self.init_kwargs = init_kwargs

        if logger is None:
            self.logger = logging.getLogger('ConfigCache')
        else:
            self.logger = logger

        self.nameserver = nameserver
        self.nameserver_port = nameserver_port
        self.host = host
        self.pyro_daemon = None
        self.pyro_id = '{}.config_generator'.format(run_id)

        self.cache: Dict[ConfigurationSpace, BaseConfigGenerator] = {}

    def run(self):
        if self.nameserver is None:
            return

        self.pyro_daemon = Pyro4.core.Daemon(host=self.host)

        with Pyro4.locateNS(host=self.nameserver, port=self.nameserver_port) as ns:
            uri = self.pyro_daemon.register(self, self.pyro_id)
            ns.register(self.pyro_id, uri)

        self.logger.info('Pyro daemon running on {}'.format(self.pyro_daemon.locationStr))

        self.pyro_daemon.requestLoop()

        with Pyro4.locateNS(self.nameserver, port=self.nameserver_port) as ns:
            ns.remove(self.pyro_id)

    def shutdown(self) -> None:
        if self.pyro_daemon is not None:
            self.pyro_daemon.shutdown()

    @Pyro4.expose
    def get(self, pipeline: FlexiblePipeline) -> BaseConfigGenerator:
        configspace = pipeline.configuration_space
        if configspace not in self.cache:
            cg = self.clazz(configspace, pipeline, **self.init_kwargs)
            self.cache[configspace] = cg
        return self.cache[configspace]

    @Pyro4.expose
    def register_result(self, job: Job):
        try:
            self.cache[job.pipeline.configuration_space].register_result(job)
        except KeyError:
            # Should never happen
            pass