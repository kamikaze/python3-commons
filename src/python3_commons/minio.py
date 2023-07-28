import urllib3
from minio import Minio
from pydantic import SecretStr


def get_client(endpoint_url: str, region_name: str, access_key_id: SecretStr, secret_access_key: SecretStr,
               secure: bool = True) -> Minio:
    http_client = urllib3.PoolManager(
        timeout=urllib3.util.Timeout(connect=300, read=300),
        maxsize=50,
        cert_reqs='CERT_REQUIRED' if secure else 'CERT_NONE',
        retries=urllib3.Retry(
            total=5,
            backoff_factor=0.2,
            status_forcelist=[500, 502, 503, 504]
        )
    )

    client = Minio(
        endpoint_url,
        region=region_name,
        access_key=access_key_id.get_secret_value(),
        secret_key=secret_access_key.get_secret_value(),
        http_client=http_client,
        secure=secure
    )

    return client
