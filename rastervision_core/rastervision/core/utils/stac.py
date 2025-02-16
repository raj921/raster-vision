from typing import List, Optional
from urllib.parse import urlparse
import logging

import boto3
from pystac import StacIO, Catalog, Item
from pystac.stac_io import DefaultStacIO
from shapely.geometry import box

from rastervision.pipeline.file_system import file_to_json

log = logging.getLogger(__name__)


def setup_stac_io() -> None:
    def read_method(uri: str):
        parsed = urlparse(uri)
        if parsed.scheme == 's3':
            bucket = parsed.netloc
            key = parsed.path[1:]
            s3 = boto3.resource('s3')
            obj = s3.Object(bucket, key)
            return obj.get()['Body'].read().decode('utf-8')
        else:
            return DefaultStacIO.read_text_method(uri)

    def write_method(uri: str, txt: str):
        parsed = urlparse(uri)
        if parsed.scheme == 's3':
            bucket = parsed.netloc
            key = parsed.path[1:]
            s3 = boto3.resource('s3')
            s3.Object(bucket, key).put(Body=txt)
        else:
            DefaultStacIO.write_text_method(uri, txt)

    StacIO.read_text_method = read_method
    StacIO.write_text_method = write_method


def is_label_item(item: Item) -> bool:
    """Resolve each extension schema into a dict, then check if it has the
    title of "Label Extension".
    """
    for ext_schema_uri in item.stac_extensions:
        schema = file_to_json(ext_schema_uri)
        if schema['title'].lower() == 'label extension':
            return True
    return False


def get_linked_image_item(label_item: Item) -> Optional[Item]:
    """Find link in the item that has "rel" == "source" and return its
    "target" item. If no such link, return None. If multiple such links,
    raise an exception."""
    links = [l for l in label_item.links if l.rel.lower() == 'source']
    if len(links) == 0:
        return None
    elif len(links) > 1:
        raise NotImplementedError()
    image_item = links[0].resolve_stac_object().target
    return image_item


def parse_stac(stac_uri: str) -> List[dict]:
    """Parse a STAC catalog JSON file to extract label URIs, images URIs,
    and AOIs.

    Note: This has been tested to be compatible with STAC version 1.0.0 but
    not any other versions.

    Args:
        stac_uri (str): Path to the STAC catalog JSON file.

    Returns:
        List[dict]: A lsit of dicts with keys: "label_uri", "image_uris",
            "label_bbox", "image_bbox", "bboxes_intersect", and "aoi_geometry".
            Each dict corresponds to one label item and its associated image
            assets in the STAC catalog.
    """
    setup_stac_io()
    cat = Catalog.from_file(stac_uri)
    version: str = cat.to_dict()['stac_version']

    if not version.startswith('1.0'):
        log.warning(f'Parsing is not guaranteed to work correctly for '
                    f'STAC version != 1.0.*. Found version: {version}.')

    cat.make_all_asset_hrefs_absolute()

    label_items = [item for item in cat.get_all_items() if is_label_item(item)]
    image_items = [get_linked_image_item(item) for item in label_items]

    if len(label_items) == 0:
        raise ValueError('Unable to find any label items in STAC catalog.')

    out = []
    for label_item, image_item in zip(label_items, image_items):
        label_uri: str = list(label_item.assets.values())[0].href
        label_bbox = box(*label_item.bbox)
        aoi_geometry: Optional[dict] = label_item.geometry

        if image_item is not None:
            image_assets = [
                asset for asset in image_item.get_assets().values()
                if 'image' in asset.media_type
            ]
            image_uris = [asset.href for asset in image_assets]
            image_bbox = box(*image_item.bbox)
            bboxes_intersect = label_bbox.intersects(image_bbox)
        else:
            image_uris = []
            image_bbox = None
            bboxes_intersect = False

        out.append({
            'label_uri': label_uri,
            'image_uris': image_uris,
            'label_bbox': label_bbox,
            'image_bbox': image_bbox,
            'bboxes_intersect': bboxes_intersect,
            'aoi_geometry': aoi_geometry
        })
    return out
