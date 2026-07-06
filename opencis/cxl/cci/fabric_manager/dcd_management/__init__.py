"""
Copyright (c) 2024-2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

from .dc_region import (
    GetHostDCRegionConfiguration,
    GetHostDCRegionConfigRequestPayload,
    GetHostDCRegionConfigResponsePayload,
    SetDCRegionConfiguration,
    SetDCRegionConfigRequestPayload,
)
from .get_dcd_info import GetDcdInfoCommand, GetDcdInfoResponsePayload
from .initiate_dynamic_capacity import (
    InitiateDynamicCapacityAdd,
    InitiateDynamicCapacityAddRequestPayload,
    InitiateDynamicCapacityRelease,
    InitiateDynamicCapacityReleaseRequestPayload,
)
from .dc_region_reference import (
    GetDcRegionExtentListsCommand,
    GetDcRegionExtentListsRequestPayload,
    GetDcRegionExtentListsResponsePayload,
    DynamicCapacityAddReferenceCommand,
    DynamicCapacityReferenceRequestPayload,
    DynamicCapacityRemoveReferenceCommand,
    DynamicCapacityListTagsCommand,
    DynamicCapacityListTagsRequestPayload,
    DynamicCapacityListTagsResponsePayload,
    DynamicCapacityTagInfoBlock,
    helper_inject_extent,
)
