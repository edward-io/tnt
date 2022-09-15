#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Iterator, Optional, Type, Union

from torch.utils.data import DataLoader, Dataset

from torchtnt.data.iterators import (
    DataIterationStrategy,
    DataIterationStrategyRegistry,
    MultiIterator,
)
from torchtnt.data.samplers import StatefulDistributedSampler

logger: logging.Logger = logging.getLogger(__name__)


class MultiDataLoader:
    """MultiDataLoader cycles through individual dataloaders passed to it.

    Attributes:
        individual_dataloaders (Dict[str, Union[DataLoader, Iterable]]): A dictionary of DataLoaders or Iterables with dataloader name as key
        and dataloader/iterable object as value.
        iteration_strategy (DataIterationStrategy): A dataclass indicating how the dataloaders are iterated over.
        iterator_cls (MultiIterator, optional): A subclass of MultiIterator defining iteration logic. This is the type, not an object instance
        ignore_empty_data (bool): skip dataloaders which contain no data. It's False by default, and an exception is raised.

    Note:
        TorchData (https://pytorch.org/data/beta/index.html) also has generic
        multi-data sources reading support to achieve the same functionability
        provided by MultiIterator.
        For example, `mux`, `mux_longest`, `cycle`, `zip` etc. Please refer
        to the documentation for more details.
    """

    def __init__(
        self,
        # pyre-fixme[24]: Generic type `Iterable` expects 1 type parameter
        individual_dataloaders: Dict[str, Union[DataLoader, Iterable]],
        iteration_strategy: DataIterationStrategy,
        iterator_cls: Optional[Type[MultiIterator]] = None,
        ignore_empty_data: bool = False,
    ) -> None:
        self.individual_dataloaders = individual_dataloaders
        self.iteration_strategy = iteration_strategy
        self.iterator_cls = iterator_cls
        for name in list(individual_dataloaders.keys()):
            try:
                next(iter(self.individual_dataloaders[name]))
            except StopIteration:
                if not ignore_empty_data:
                    raise ValueError(f"DataLoader '{name}' contains no data.")
                else:
                    logger.warning(
                        f"DataLoader '{name}' which contains no data. "
                        "You might have empty dataloaders in the input dict."
                    )

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Iterator functions for the collection of dataloaders

        Returns:
            a newly created iterator based on DataIterationStrategy

        """
        iterator_cls = self.iterator_cls
        if iterator_cls is None:
            iterator_cls = DataIterationStrategyRegistry.get(self.iteration_strategy)
        # pyre-fixme[16]: `MultiDataLoader` has no attribute `iterator`.
        # pyre-fixme[45]: Cannot instantiate abstract class `MultiIterator`.
        self.iterator = iterator_cls(
            individual_dataloaders=self.individual_dataloaders,
            iteration_strategy=self.iteration_strategy,
        )
        return self.iterator


class StatefulDataLoader(DataLoader):
    """
    StatefulDataLoader is a wrapper around PyTorch DataLoader that supports saving and loading
    state. This is useful for checkpointing and resuming training.

    Note::
        StatefulDataLoader expects all ranks to have the same number of batches per epoch and data to be read in lockstep.


    Example::

    Args:
        dataset (Dataset): dataset to load data from
        sampler (StatefulDistributedSampler): sampler to use for loading data. Currently only StatefulDistributedSampler is supported.
        *args: args to pass to DataLoader
        **kwargs: kwargs to pass to DataLoader
    """

    def __init__(
        self, dataset: Dataset, sampler: StatefulDistributedSampler, *args, **kwargs
    ):
        super().__init__(dataset, sampler=sampler, *args, **kwargs)

    def __iter__(self):
        self._iter = super().__iter__()
        return self._iter

    def state_dict(self):
        return {
            "sampler": self.sampler.state_dict(),
            "start_index": self._iter._num_yielded
            * self.batch_size
            * self.sampler.world_size,
        }

    def load_state_dict(self, state_dict):
        state_dict["sampler"].update({"start_index": state_dict["start_index"]})
        self.sampler.load_state_dict(state_dict["sampler"])
