"""Post-processing algorithms for indexed raster layers.

titiler already runs a post-process step on every mosaic route — `?algorithm=`
and `?algorithm_params=`, applied *after* the mosaic is merged and before it is
rendered. That timing is the whole reason this is an algorithm and not band math:
`expression=` is evaluated per asset *before* the merge, so filtering there would
let a high-priority raster that lacks a class punch holes through one that has
it. Post-merge, compositing is untouched and only the finished tile is filtered.
"""

from typing import Optional, Union

import numpy
from fastapi import HTTPException
from rio_tiler.models import ImageData
from titiler.core.algorithm import Algorithms
from titiler.core.algorithm import algorithms as default_algorithms
from titiler.core.algorithm.base import BaseAlgorithm

from macrostrat.utils import get_logger

log = get_logger(__name__)

__all__ = ["ClassFilter", "categorical_algorithms", "CLASS_FILTER_NAME"]

# The name a client asks for: `?algorithm=classes`. A cross-repo contract with
# any client that builds these URLs.
CLASS_FILTER_NAME = "classes"


class ClassFilter(BaseAlgorithm):
    """Show only the named (or numbered) classes of a categorical raster.

    Classes may be given as labels, resolved against the layer's class
    vocabulary, or as raw integer values for a layer that has no vocabulary:

        ?algorithm=classes&algorithm_params={"classes":["Kaolinite","Alunite"]}

    Excluded pixels are *masked*, not recolored, so they render transparent
    however the layer is styled — `rio_tiler.utils.render` takes the alpha
    channel from both the image mask and the colormap. Values are left alone, so
    the layer's own palette still colors what survives and a multi-class
    selection reads as a legend rather than a single highlight color.
    """

    title: str = "Filter categorical classes"
    description: str = (
        "Mask everything except the given classes, named or numbered. "
        "Class values are preserved, so the layer's palette still applies."
    )

    classes: list[Union[str, int]] = []

    def __call__(self, img: ImageData) -> ImageData:
        """Mask every pixel outside the requested classes."""
        if not self.classes:
            return img

        values = _resolve(self.classes, img.metadata or {})

        array = img.array.copy()
        # A pixel survives if any band holds a requested value. Classifications
        # are single-band in practice; this keeps a multi-band one sensible
        # rather than silently wrong.
        keep = numpy.isin(array.data, list(values))
        if array.shape[0] > 1:
            keep = numpy.logical_or.reduce(keep, axis=0)
            keep = numpy.broadcast_to(keep, array.shape)
        array.mask = numpy.logical_or(array.mask, ~keep)

        # `metadata` must be forwarded: the layer's colormap rides on it, and
        # dropping it here would render the filtered tile in grayscale.
        return ImageData(
            array,
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_names=img.band_names,
            metadata=img.metadata,
            cutline_mask=img.cutline_mask,
        )


def _resolve(requested: list[Union[str, int]], metadata: dict) -> set[int]:
    """Class values for a list of labels and/or integers.

    An unresolvable label is a 400 listing what is available, rather than an
    empty tile: a misspelled mineral and a mineral that genuinely isn't in view
    look identical otherwise.
    """
    labels = [str(c["label"]) for c in metadata.get("categories") or []]
    by_label = {
        str(c["label"]).casefold(): int(c["value"])
        for c in metadata.get("categories") or []
    }

    values = set()
    for entry in requested:
        if isinstance(entry, int):
            values.add(entry)
            continue
        text = str(entry).strip()
        if text.casefold() in by_label:
            values.add(by_label[text.casefold()])
            continue
        # Numbers survive the trip through JSON as strings often enough to be
        # worth accepting.
        if _is_int(text):
            values.add(int(text))
            continue
        if not by_label:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown class {text!r}: this layer has no class vocabulary, "
                    "so classes must be given as integers. Set one with "
                    "`macrostrat raster set-categories`."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=f"Unknown class {text!r}. Available: {labels}",
        )
    return values


def _is_int(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True


def categorical_algorithms(extra: Optional[dict] = None) -> Algorithms:
    """titiler's algorithm registry, plus the ones these layers add.

    A registry rather than a mutation of the global one, so mounting a raster
    layer doesn't change what an unrelated `TilerFactory` in the same
    application advertises.
    """
    registry = default_algorithms.register({CLASS_FILTER_NAME: ClassFilter})
    if extra:
        registry = registry.register(extra)
    return registry
