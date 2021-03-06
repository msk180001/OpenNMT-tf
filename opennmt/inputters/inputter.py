"""Define generic inputters."""

import abc
import six

import tensorflow as tf

from opennmt.layers.reducer import ConcatReducer, JoinReducer
from opennmt.utils.misc import extract_prefixed_keys, extract_suffixed_keys


@six.add_metaclass(abc.ABCMeta)
class Inputter(object):
  """Base class for inputters."""

  def __init__(self, dtype=tf.float32):
    self.volatile = set()
    self.process_hooks = []
    self.dtype = dtype
    self.is_target = False

  @property
  def num_outputs(self):
    """How many parallel outputs does this inputter produce."""
    return 1

  def initialize(self, metadata, asset_dir=None, asset_prefix=""):
    """Initializes the inputter within the current graph.

    For example, one can create lookup tables in this method
    for their initializer to be added to the current graph
    ``TABLE_INITIALIZERS`` collection.

    Args:
      metadata: A dictionary containing additional metadata set
        by the user.
      asset_dir: The directory where assets can be written. If ``None``, no
        assets are returned.
      asset_prefix: The prefix to attach to assets filename.

    Returns:
      A dictionary containing additional assets used by the inputter.
    """
    _ = metadata
    _ = asset_dir
    _ = asset_prefix
    return {}

  @abc.abstractmethod
  def make_dataset(self, data_file, training=None):
    """Creates the dataset required by this inputter.

    Args:
      data_file: The data file.
      training: Run in training mode.

    Returns:
      A ``tf.data.Dataset``.
    """
    raise NotImplementedError()

  @abc.abstractmethod
  def get_dataset_size(self, data_file):
    """Returns the size of the dataset.

    Args:
      data_file: The data file.

    Returns:
      The total size.
    """
    raise NotImplementedError()

  def get_serving_input_receiver(self):
    """Returns a serving input receiver for this inputter.

    Returns:
      A ``tf.estimator.export.ServingInputReceiver``.
    """
    if self.is_target:
      raise ValueError("Target inputters do not define a serving input")
    receiver_tensors = self.get_receiver_tensors()
    if receiver_tensors is None:
      raise NotImplementedError("This inputter does not define receiver tensors.")
    features = self.make_features(features=receiver_tensors.copy())
    return tf.estimator.export.ServingInputReceiver(features, receiver_tensors)

  def get_receiver_tensors(self):
    """Returns the input placeholders for serving."""
    return None

  def get_length(self, features):
    """Returns the length of the input features, if defined."""
    return features.get("length")

  @abc.abstractmethod
  def make_features(self, element=None, features=None, training=None):
    """Creates features from data.

    Args:
      element: An element from the dataset.
      features: An optional dictionary of features to augment.
      training: Run in training mode.

    Returns:
      A dictionary of ``tf.Tensor``.
    """
    raise NotImplementedError()

  def make_inputs(self, features, training=None):
    """Creates the model input from the features.

    Args:
      features: A dictionary of ``tf.Tensor``.
      training: Run in training mode.

    Returns:
      The model input.
    """
    _ = training
    return features

  def visualize(self, log_dir):
    """Visualizes the transformation, usually embeddings.

    Args:
      log_dir: The active log directory.
    """
    _ = log_dir
    return


  # TODO: remove the following methods at some point.

  def set_data_field(self, data, key, value, volatile=False):
    """Sets a data field.

    Args:
      data: The data dictionary.
      key: The value key.
      value: The value to assign.
      volatile: If ``True``, the key/value pair will be removed once the
        processing done.

    Returns:
      The updated data dictionary.
    """
    data[key] = value
    if volatile:
      self.volatile.add(key)
    return data

  def remove_data_field(self, data, key):
    """Removes a data field.

    Args:
      data: The data dictionary.
      key: The value key.

    Returns:
      The updated data dictionary.
    """
    del data[key]
    return data

  def add_process_hooks(self, hooks):
    """Adds processing hooks.

    Processing hooks are additional and model specific data processing
    functions applied after calling this inputter
    :meth:`opennmt.inputters.inputter.Inputter.process` function.

    Args:
      hooks: A list of callables with the signature
        ``(inputter, data) -> data``.
    """
    self.process_hooks.extend(hooks)

  def process(self, data, training=None):
    """Prepares raw data.

    Args:
      data: The raw data.
      training: Run in training mode.

    Returns:
      A dictionary of ``tf.Tensor``.

    See Also:
      :meth:`opennmt.inputters.inputter.Inputter.transform_data`
    """
    data = self.make_features(data, training=training)
    for hook in self.process_hooks:
      data = hook(self, data)
    for key in self.volatile:
      data = self.remove_data_field(data, key)
    self.volatile.clear()
    return data

  def transform_data(self, data, mode=tf.estimator.ModeKeys.TRAIN, log_dir=None):
    """Transforms the processed data to an input.

    This is usually a simple forward of a :obj:`data` field to
    :meth:`opennmt.inputters.inputter.Inputter.transform`.

    See also `process`.

    Args:
      data: A dictionary of data fields.
      mode: A ``tf.estimator.ModeKeys`` mode.
      log_dir: The log directory. If set, visualization will be setup.

    Returns:
      The transformed input.
    """
    inputs = self.make_inputs(data, training=mode == tf.estimator.ModeKeys.TRAIN)
    if log_dir:
      self.visualize(log_dir)
    return inputs


@six.add_metaclass(abc.ABCMeta)
class MultiInputter(Inputter):
  """An inputter that gathers multiple inputters."""

  def __init__(self, inputters, reducer=None):
    if not isinstance(inputters, list) or not inputters:
      raise ValueError("inputters must be a non empty list")
    dtype = inputters[0].dtype
    for inputter in inputters:
      if inputter.dtype != dtype:
        raise TypeError("All inputters must have the same dtype")
    super(MultiInputter, self).__init__(dtype=dtype)
    self.inputters = inputters
    self.reducer = reducer

  @property
  def num_outputs(self):
    if self.reducer is None or isinstance(self.reducer, JoinReducer):
      return len(self.inputters)
    return 1

  def initialize(self, metadata, asset_dir=None, asset_prefix=""):
    assets = {}
    for i, inputter in enumerate(self.inputters):
      assets.update(inputter.initialize(
          metadata, asset_dir=asset_dir, asset_prefix="%s%d_" % (asset_prefix, i + 1)))
    return assets

  @abc.abstractmethod
  def make_dataset(self, data_file, training=None):
    raise NotImplementedError()

  @abc.abstractmethod
  def get_dataset_size(self, data_file):
    raise NotImplementedError()

  def visualize(self, log_dir):
    for i, inputter in enumerate(self.inputters):
      with tf.variable_scope("inputter_{}".format(i)):
        inputter.visualize(log_dir)


class ParallelInputter(MultiInputter):
  """An multi inputter that process parallel data."""

  def __init__(self, inputters, reducer=None, combine_features=True):
    """Initializes a parallel inputter.

    Args:
      inputters: A list of :class:`opennmt.inputters.inputter.Inputter`.
      reducer: A :class:`opennmt.layers.reducer.Reducer` to merge all inputs. If
        set, parallel inputs are assumed to have the same length.
      combine_features: Combine each inputter features in a single dict or
        return them separately. This is typically ``True`` for multi source
        inputs but ``False`` for features/labels parallel data.
    """
    super(ParallelInputter, self).__init__(inputters, reducer=reducer)
    self.combine_features = combine_features

  def make_dataset(self, data_file, training=None):
    if not isinstance(data_file, list) or len(data_file) != len(self.inputters):
      raise ValueError("The number of data files must be the same as the number of inputters")
    datasets = [
        inputter.make_dataset(data, training=training)
        for inputter, data in zip(self.inputters, data_file)]
    return tf.data.Dataset.zip(tuple(datasets))

  def get_dataset_size(self, data_file):
    if not isinstance(data_file, list) or len(data_file) != len(self.inputters):
      raise ValueError("The number of data files must be the same as the number of inputters")
    dataset_sizes = [
        inputter.get_dataset_size(data)
        for inputter, data in zip(self.inputters, data_file)]
    dataset_size = dataset_sizes[0]
    for size in dataset_sizes:
      if size != dataset_size:
        raise RuntimeError("The parallel data files do not have the same size")
    return dataset_size

  def get_receiver_tensors(self):
    receiver_tensors = {}
    for i, inputter in enumerate(self.inputters):
      tensors = inputter.get_receiver_tensors()
      for key, value in six.iteritems(tensors):
        receiver_tensors["{}_{}".format(key, i)] = value
    return receiver_tensors

  def get_length(self, features):
    lengths = []
    for i, inputter in enumerate(self.inputters):
      if self.combine_features:
        sub_features = extract_prefixed_keys(features, "inputter_{}_".format(i))
      else:
        sub_features = features[i]
      lengths.append(inputter.get_length(sub_features))
    if self.reducer is None:
      return lengths
    else:
      return lengths[0]

  def make_features(self, element=None, features=None, training=None):
    if self.combine_features:
      if features is None:
        features = {}
      for i, inputter in enumerate(self.inputters):
        prefix = "inputter_%d_" % i
        sub_features = extract_prefixed_keys(features, prefix)
        if not sub_features:
          # Also try to read the format produced by get_receiver_tensors.
          sub_features = extract_suffixed_keys(features, "_%d" % i)
        sub_features = inputter.make_features(
            element=element[i] if element is not None else None,
            features=sub_features,
            training=training)
        for key, value in six.iteritems(sub_features):
          features["%s%s" % (prefix, key)] = value
      return features
    else:
      if features is None:
        features = [{} for _ in self.inputters]
      for i, inputter in enumerate(self.inputters):
        features[i] = inputter.make_features(
            element=element[i] if element is not None else None,
            features=features[i],
            training=training)
      return tuple(features)

  def make_inputs(self, features, training=None):
    transformed = []
    for i, inputter in enumerate(self.inputters):
      with tf.variable_scope("inputter_{}".format(i)):
        if self.combine_features:
          sub_features = extract_prefixed_keys(features, "inputter_{}_".format(i))
        else:
          sub_features = features[i]
        transformed.append(inputter.make_inputs(sub_features, training=training))
    if self.reducer is not None:
      transformed = self.reducer(transformed)
    return transformed


class MixedInputter(MultiInputter):
  """An multi inputter that applies several transformation on the same data."""

  def __init__(self,
               inputters,
               reducer=ConcatReducer(),
               dropout=0.0):
    """Initializes a mixed inputter.

    Args:
      inputters: A list of :class:`opennmt.inputters.inputter.Inputter`.
      reducer: A :class:`opennmt.layers.reducer.Reducer` to merge all inputs.
      dropout: The probability to drop units in the merged inputs.
    """
    super(MixedInputter, self).__init__(inputters, reducer=reducer)
    self.dropout = dropout

  def make_dataset(self, data_file, training=None):
    return self.inputters[0].make_dataset(data_file, training=training)

  def get_dataset_size(self, data_file):
    return self.inputters[0].get_dataset_size(data_file)

  def get_receiver_tensors(self):
    receiver_tensors = {}
    for inputter in self.inputters:
      receiver_tensors.update(inputter.get_receiver_tensors())
    return receiver_tensors

  def get_length(self, features):
    return self.inputters[0].get_length(features)

  def make_features(self, element=None, features=None, training=None):
    if features is None:
      features = {}
    for inputter in self.inputters:
      features = inputter.make_features(
          element=element, features=features, training=training)
    return features

  def make_inputs(self, features, training=None):
    transformed = []
    for i, inputter in enumerate(self.inputters):
      with tf.variable_scope("inputter_{}".format(i)):
        transformed.append(inputter.make_inputs(features, training=training))
    outputs = self.reducer(transformed)
    outputs = tf.layers.dropout(outputs, rate=self.dropout, training=training)
    return outputs


class ExampleInputter(ParallelInputter):
  """An inputter that returns training examples (parallel features and labels)."""

  def __init__(self, features_inputter, labels_inputter):
    """Initializes this inputter.

    Args:
      features_inputter: An inputter producing the features (source).
      labels_inputter: An inputter producing the labels (target).
    """
    self.features_inputter = features_inputter
    self.labels_inputter = labels_inputter
    self.labels_inputter.is_target = True
    super(ExampleInputter, self).__init__(
        [self.features_inputter, self.labels_inputter], combine_features=False)

  def initialize(self, metadata, asset_dir=None, asset_prefix=""):
    assets = {}
    assets.update(self.features_inputter.initialize(
        metadata, asset_dir=asset_dir, asset_prefix="source_"))
    assets.update(self.labels_inputter.initialize(
        metadata, asset_dir=asset_dir, asset_prefix="target_"))
    return assets
