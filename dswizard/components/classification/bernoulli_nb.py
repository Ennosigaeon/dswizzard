import numpy as np
from ConfigSpace.configuration_space import ConfigurationSpace
from ConfigSpace.hyperparameters import UniformFloatHyperparameter, CategoricalHyperparameter

from dswizard.components.base import PredictionAlgorithm
from dswizard.util.common import check_for_bool


class BernoulliNB(PredictionAlgorithm):

    def __init__(self, alpha: float = 1.0, fit_prior: bool = True, random_state=None, verbose: int = 0):
        super().__init__()
        self.alpha = alpha
        self.fit_prior = fit_prior
        self.random_state = random_state
        self.verbose = int(verbose)
        self.classes_ = None

    def fit(self, X, y):
        import sklearn.naive_bayes

        self.fit_prior = check_for_bool(self.fit_prior)
        self.estimator = sklearn.naive_bayes.BernoulliNB(alpha=self.alpha, fit_prior=self.fit_prior)
        self.classes_ = np.unique(y.astype(int))

        # Fallback for multilabel classification
        if len(y.shape) > 1 and y.shape[1] > 1:
            import sklearn.multiclass
            self.estimator = sklearn.multiclass.OneVsRestClassifier(self.estimator, n_jobs=1)

        self.estimator.fit(X, y)

        return self

    @staticmethod
    def get_properties(dataset_properties=None):
        return {'shortname': 'BernoulliNB',
                'name': 'Bernoulli Naive Bayes classifier',
                'handles_regression': False,
                'handles_classification': True,
                'handles_multiclass': True,
                'handles_multilabel': True,
                'is_deterministic': True,
                # 'input': (DENSE, SPARSE, UNSIGNED_DATA),
                # 'output': (PREDICTIONS,)
                }

    @staticmethod
    def get_hyperparameter_search_space(dataset_properties=None):
        cs = ConfigurationSpace()

        # the smoothing parameter is a non-negative float
        # I will limit it to 1000 and put it on a logarithmic scale. (SF)
        # Please adjust that, if you know a proper range, this is just a guess.
        alpha = UniformFloatHyperparameter(name="alpha", lower=1e-2, upper=100, default_value=1, log=True)
        fit_prior = CategoricalHyperparameter(name="fit_prior", choices=[True, False], default_value=True)

        cs.add_hyperparameters([alpha, fit_prior])
        return cs
