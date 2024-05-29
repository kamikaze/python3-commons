import logging
import tarfile
from datetime import date, timedelta
from io import BytesIO

from python3_commons import object_storage
from python3_commons.conf import s3_settings

logger = logging.getLogger(__name__)


async def archive_audit_data(root_path: str = 'audit'):
    today = date.today() - timedelta(days=1)
    year = today.year
    month = today.month
    day = today.day
    bucket_name = s3_settings.s3_bucket
    fo = BytesIO()
    object_names = []
    date_path = object_storage.get_absolute_path(f'{root_path}/{year}/{month:02}/{day:02}')

    with tarfile.open(fileobj=fo, mode='w|bz2') as archive:
        if objects := object_storage.get_objects(bucket_name, date_path, recursive=True):
            logger.info(f'Compacting files in: {date_path}')

            for name, last_modified, content in objects:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mtime = last_modified.timestamp()
                archive.addfile(info, BytesIO(content))
                object_names.append(name)

    fo.seek(0)

    if object_names:
        archive_path = object_storage.get_absolute_path(
            f'.archive/{year}_{month:02}/{year}_{month:02}_{day:02}.tar.bz2')
        object_storage.put_object(bucket_name, archive_path, fo, fo.getbuffer().nbytes)

        if errors := object_storage.remove_objects(bucket_name, object_names=object_names):
            for error in errors:
                logger.error(f'Failed to delete object in {bucket_name=}: {error}')
    else:
        logger.info('No objects to archive found.')
