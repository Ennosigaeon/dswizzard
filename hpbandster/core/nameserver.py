import os
import pickle
import threading
from typing import Tuple

import Pyro4.naming
import Pyro4


def nic_name_to_host(nic_name):
    """ helper function to translate the name of a network card into a valid host name"""
    from netifaces import ifaddresses, AF_INET
    host = ifaddresses(nic_name).setdefault(AF_INET, [{'addr': 'No IP addr'}])[0]['addr']
    return host


class NameServer(object):
    """
    The nameserver serves as a phonebook-like lookup table for your workers. Unique names are created so the workers
    can work in parallel and register their results without creating racing conditions. The implementation uses
    `PYRO4 <https://pythonhosted.org/Pyro4/nameserver.html>`_ as a backend and this class is basically a wrapper.
    """

    def __init__(self,
                 run_id: str,
                 working_directory: str = None,
                 host: str = None,
                 port: int = 0,
                 nic_name: str = None):
        """

        :param run_id: unique run_id associated with the HPB run
        :param working_directory: path to the working directory of the HPB run to store the nameservers credentials.
            If None, no config file will be written.
        :param host: the hostname to use for the nameserver
        :param port: the port to be used. Default (=0) means a random port
        :param nic_name: name of the network interface to use (only used if host is not given)
        """

        self.run_id = run_id
        self.host = host
        self.nic_name = nic_name
        self.port = port
        self.dir = working_directory
        self.conf_fn = None
        self.pyro_ns = None

    def start(self) -> Tuple[str, int]:
        """
        starts a Pyro4 nameserver in a separate thread
        :return: the host name and the used port
        """

        if self.host is None:
            if self.nic_name is None:
                self.host = 'localhost'
            else:
                self.host = nic_name_to_host(self.nic_name)

        Pyro4.config.SERIALIZER = 'pickle'
        Pyro4.config.SERIALIZERS_ACCEPTED = ['json', 'marshal', 'serpent', 'pickle']
        uri, self.pyro_ns, _ = Pyro4.naming.startNS(host=self.host, port=self.port)

        self.host, self.port = self.pyro_ns.locationStr.split(':')
        self.port = int(self.port)

        thread = threading.Thread(target=self.pyro_ns.requestLoop, name='Pyro4 nameserver started by HpBandSter')
        thread.start()

        if self.dir is not None:
            os.makedirs(self.dir, exist_ok=True)
            self.conf_fn = os.path.join(self.dir, 'HPB_run_{}_pyro.pkl'.format(self.run_id))

            with open(self.conf_fn, 'wb') as fh:
                pickle.dump((self.host, self.port), fh)

        return self.host, self.port

    def shutdown(self) -> None:
        """
        clean shutdown of the nameserver and the config file (if written)
        """
        if self.pyro_ns is not None:
            self.pyro_ns.shutdown()
            self.pyro_ns = None

        if self.conf_fn is not None:
            os.remove(self.conf_fn)
            self.conf_fn = None

    def __del__(self):
        self.shutdown()
