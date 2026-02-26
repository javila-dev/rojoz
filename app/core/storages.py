from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage
from urllib.parse import urlsplit, urlunsplit


class PublicMediaStorage(S3Boto3Storage):
    bucket_name = settings.AWS_PUBLIC_MEDIA_BUCKET
    default_acl = "public-read"
    querystring_auth = False
    # For MinIO path-style access in the browser
    custom_domain = (
        f"{settings.AWS_S3_CUSTOM_DOMAIN}/{settings.AWS_PUBLIC_MEDIA_BUCKET}"
        if getattr(settings, "AWS_S3_CUSTOM_DOMAIN", None)
        else None
    )


class PrivateMediaStorage(S3Boto3Storage):
    bucket_name = settings.AWS_PRIVATE_MEDIA_BUCKET
    default_acl = "private"
    querystring_auth = True
    # IMPORTANT:
    # Keep custom_domain=None for private files so django-storages generates
    # signed S3/MinIO URLs. Using custom_domain here may produce unsigned URLs.
    custom_domain = None

    def url(self, name, parameters=None, expire=None, http_method=None):
        signed_url = super().url(
            name,
            parameters=parameters,
            expire=expire,
            http_method=http_method,
        )

        # Optional host rewrite: useful when backend signs against an internal
        # endpoint (e.g. minio:9000) but clients need a public domain.
        public_host = getattr(settings, "AWS_S3_PRIVATE_CUSTOM_DOMAIN", "") or getattr(
            settings, "AWS_S3_CUSTOM_DOMAIN", ""
        )
        if not public_host:
            return signed_url

        parsed = urlsplit(signed_url)
        if not parsed.netloc:
            return signed_url

        protocol = getattr(settings, "AWS_S3_URL_PROTOCOL", "https:").rstrip(":")
        new_netloc = public_host.split("/")[0]
        rewritten = parsed._replace(scheme=protocol, netloc=new_netloc)
        return urlunsplit(rewritten)
