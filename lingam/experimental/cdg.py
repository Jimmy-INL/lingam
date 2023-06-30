import numpy as np
import pandas as pd
import scipy.stats as st

from sklearn.base import is_regressor, is_classifier
from sklearn.utils import check_array, check_random_state
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LinearRegression, LogisticRegression


class CausalDataGenerator:
    """ CausalDataGenerator
    """
    
    def __init__(self, random_state=None):
        """ Construct a CausalDataGenerator.

        Parameters
        ----------
        random_state : None, int or instance of RandomState, default=None


        Attributes
        ----------
        model_cause_ : estimator object of scikit-learn
            Trained model of cause. Access will be available after fitting.

        model_effect_ : estimator object of scikit-learn
            Trained model of effect. Access will be available after fitting.

        error_cause_ : pandas.Series
            Error in the cause model. Access will be available after fitting.

        error_effect_ : pandas.Series
            Error in the effect model. Access will be available after fitting.
        """
        random_state = check_random_state(random_state)
        self._random_state = random_state
    
    def fit(self, X, cause, effect, adjustments=None, model_cause=None, model_effect=None):
        """Fit models to the data.

        Parameters
        ----------
        X : pandas.DataFrame (n_samples, n_features)
            The input data. Columns with dtype of "category" are treated as categorical variables.

        cause : str
            Name of the variable to be treated as "cause". cause shall be an element of X.columns.

        effect : str
            Name of the variable to be treated as "effect". effect shall be an element of X.columns.

        adjustments : array-like of str, optional, default=None
            Names of variables to be treated as "adjustment". Each element of adjustments
            shall be an element of X.columns.

        model_cause : estimator object of scikit-learn, optional, default=None
            Model of "cause". A regression model must be set up when the objective variable is
            a continuous variable, and a classification model must be set up when the objective
            variable is a discrete variable.

        model_effect : estimator object of scikit-learn, optional, default=None
            Model of "effect". A regression model must be set up when the objective variable is
            a continuous variable, and a classification model must be set up when the objective
            variable is a discrete variable.

        Returns
        -------
        self : object
            Instance of fitted estimator.
        """

        if not isinstance(X, pd.DataFrame):
            raise TypeError("X shall be a pandas.DataFrame.")
            
        check_array(X, dtype=None, ensure_min_features=2)
        X = X.copy()
        
        if cause not in X.columns:
            raise ValueError("cause isn't exist in X.columns.")
            
        if effect not in X.columns:
            raise ValueError("effect isn't exist in X.columns.")
            
        if adjustments is None:
            adjustments = np.array([])
        else:
            adjustments = check_array(
                adjustments,
                ensure_2d=False,
                dtype=None,
                ensure_min_samples=0
            ).reshape((-1, ))
            
        if model_cause is not None:
            is_categorical = X[cause].dtype == "category"
            self._is_sklearn_estimator(model_cause, is_categorical)
            
        if model_effect is not None:
            is_categorical = X[effect].dtype == "category"
            self._is_sklearn_estimator(model_effect, is_categorical)
        
        # cause
        if len(adjustments) == 0:
            model_cause, resid_cause = None, X[cause]
        else:
            model_cause, resid_cause = self._make_model(
                pd.DataFrame(X[adjustments]), X[cause], "model_cause", model=model_cause)
        
        # effect
        model_effect, resid_effect = self._make_model(
            pd.DataFrame(X[[cause, *adjustments]]), X[effect], "model_effect", model=model_effect)
        
        # arguments
        self._X = X
        self._cause = cause
        self._effect = effect
        self._adjustments = adjustments
        
        # estimated values
        self.model_cause_ = model_cause
        self.model_effect_ = model_effect
        self.error_cause_ = resid_cause
        self.error_effect_ = resid_effect
        
        return self
    
    def generate(
        self,
        interv_endog=None,
        interv_exog=None,
        cause_model=None,
        cause_model_args=None,
        effect_model=None,
        effect_model_args=None
    ):
        """
        Parameters
        ----------
        interv_endog : dict, optional (default=None)
            The key is the name of the target variable. The data is generated by replacing
            the specified endogenous variable with the value.
            {'name': str, 'values': array-like}.

        interv_exog : array_like, optional (default=None)
            The key is the name of the target variable. The data is generated by replacing
            the specified exogenous variable with the value.
            {'name': str, 'values': array-like}.

        cause_model : callable, optional (default=None)
            The estimated model is replaced with ``cause_model``. The arguments are 
            (X, error, cause_model_args). ``X`` and ``error` are a pandas.DataFrame.
            The output of ``cause_model`` shall be an array-like and its length shall be
            the same as input data.
            ``error`` is residuals of ``cause``.

        cause_model_args : object, optional (default=None)
            Arguments of ``cause_model``.

        effect_model : callable, optional (default=None)
            Replaces the model that generates ``effect``.

        effect_model_args : object, optional (default=None)
            Arguments of ``effect_model``.
        
        Return
        ------
        generated_values : pd.Series
        """

        # check arguments
        interv_endog = self._check_data_dict(interv_endog, "interv_endog")
        interv_exog = self._check_data_dict(interv_exog, "interv_exog")
        
        if cause_model is not None:   
            if not callable(cause_model):
                raise TypeError("cause_model shall be callable.")
                
        if effect_model is not None:   
            if not callable(effect_model):
                raise TypeError("effect_model shall be callable.")
                    
        # initial data
        generated = self._X[[self._cause, self._effect, *self._adjustments]].copy()
        
        # update errors
        generated[self._cause] = self.error_cause_
        generated[self._effect] = self.error_effect_
        
        # update interventions
        for name, interv_exog_ in interv_exog.items():
            generated[name] = interv_exog_
        
        # cause
        if self._cause in interv_endog.keys():
            generated[self._cause] = interv_endog[self._cause]
        else:
            if cause_model is not None:
                try:
                    predicted = cause_model(
                        generated[self._adjustments],
                        generated[self._cause],
                        cause_model_args
                    )
                except Exception as e:
                    raise RuntimeError("Exception: cause_model: " + str(e))

                generated[self._cause] = predicted
                if self._X[self._cause].dtype == "category":
                    generated[self._cause] = pd.Categorical(generated[self._cause])
            else:
                if self._X[self._cause].dtype != "category":
                    predicted = self.model_cause_.predict(generated[self._adjustments])
                    generated[self._cause] += np.array(predicted)
                else:
                    proba = self.model_cause_.predict_proba(generated[self._adjustments])
                    
                    predicted = []
                    for proba_ in proba:
                        predicted.append(self._random_state.choice(self.model_cause_.classes_, p=proba_))
                    generated[self._cause] = np.array(predicted)
                    generated[self._cause] = pd.Categorical(generated[self._cause])
        
        # effect
        if self._effect in interv_endog.keys():
            generated[self._effect] = interv_endog[self._effect]
        else:
            if effect_model is not None:
                try:
                    predicted = effect_model(
                        generated[[self._cause, *self._adjustments]],
                        generated[self._effect],
                        effect_model_args
                    )
                except Exception as e:
                    raise RuntimeError("Exception: effect_model: " + str(e))

                generated[self._effect] = predicted
                if self._X[self._effect].dtype == "category":
                    generated[self._effect] = pd.Categorical(generated[self._effect])
            else:
                if self._X[self._effect].dtype != "category":
                    predicted = self.model_effect_.predict(generated[[self._cause, *self._adjustments]])
                    generated[self._effect] += np.array(predicted)
                else:
                    proba = self.model_effect_.predict_proba(generated[[self._cause, *self._adjustments]])
                    
                    predicted = []
                    for proba_ in proba:
                        predicted.append(self._random_state.choice(self.model_effect_.classes_, p=proba_))
                    generated[self._effect] = np.array(predicted)
                    generated[self._effect] = pd.Categorical(generated[self._effect])
        
        generated_values = generated.loc[self._X.index, self._X.columns]
        
        return generated_values

    def _is_sklearn_estimator(self, estimator, is_categorical):
        if is_categorical is True and is_regressor(estimator):
            raise TypeError("The Object variable is categorical but the estimator is a regressor.")
        elif is_categorical is False and is_classifier(estimator):
            raise TypeError("The Object variable is not categorical but the estimator is a classifier.")
            
        if is_categorical is True:
            try:
                func = getattr(estimator, "predict_proba")
                if not callable(func):
                    raise Exception
            except Exception:
                raise RuntimeError(
                    "Classification models shall have "
                    + "predict_proba()."
                )
                
    def _make_model(self, X, y, name, model=None):
        if model is not None:
            try:
                model.fit(X, y)
            except Exception as e:
                raise RuntimeError(f"{name}.fit(): {str(e)}")
        else:
            if y.dtypes == "category":
                model = LogisticRegression()
            else:
                model = LinearRegression()

            categoricals = X.columns[X.dtypes == "category"]
            numerics = X.columns[X.dtypes != "category"]
            
            if len(categoricals) > 0:
                transformers = [
                    ("categorical", OneHotEncoder(sparse=False), categoricals),
                    ("numeric", "passthrough", numerics),
                ]
                trans = ColumnTransformer(transformers=transformers)

                model = Pipeline([
                    ("transformer", trans),
                    ("estimator", model)
                ])
        
            model.fit(X, y)
        
        if y.dtype == "category":
            resids = np.full(X.shape[0], np.nan)
        else:
            resids = y - model.predict(X)
        resids = pd.Series(resids, index=y.index, name=y.name)
        
        return model, resids
    
    def _check_data_dict(self, data_dict, name):
        if data_dict is None:
            return {}

        if not isinstance(data_dict, dict):
            raise TypeError(f"{name} shall be a dictionary.")

        for name, values in data_dict.items():
            if not isinstance(name, str):
                raise TypeError(f"A key of {name} shall be str.")

            if name not in [self._cause, self._effect, *self._adjustments]:
                raise ValueError(f"Keys of {name} shall be cause or effect or a element of adjustments.")

            values = check_array(values, ensure_2d=False, dtype=None)
            if values.shape[0] != self._X.shape[0]:
                raise ValueError(f"shape[0] of {name} shall be the same as X.shape[0].")
                    
        return data_dict