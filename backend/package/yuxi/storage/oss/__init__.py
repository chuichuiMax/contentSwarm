"""阿里云 OSS 对象存储（SDK V2）。"""

from .client import OssStorageClient, RoutedStorageClient, is_oss_storage_enabled, oss_logical_buckets

__all__ = ["OssStorageClient", "RoutedStorageClient", "is_oss_storage_enabled", "oss_logical_buckets"]
