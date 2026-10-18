"""Azure Schema Registry Avro encoder construction; credentials stay outside URIs."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from shape.errors import ShapeError
from shape.streaming.binary import AzureAvroCodec


def azure_codec(
    namespace: str,
    *,
    credential: Any = None,
    group: str | None = None,
    schema_text: str | None = None,
    encoder: Any = None,
) -> AzureAvroCodec:
    """Build the optional SDK codec; the SDK caches schemas by content-type id."""
    parts = urlsplit(namespace if "://" in namespace else "https://" + namespace)
    if (
        parts.username
        or parts.password
        or not parts.hostname
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.scheme != "https"
    ):
        raise ShapeError("schema_registry_url must be an Azure namespace without credentials")
    resolve = None
    if encoder is None:
        try:
            from azure.schemaregistry import SchemaRegistryClient
            from azure.schemaregistry.encoder.avroencoder import AvroEncoder
        except ImportError as exc:
            raise ShapeError("install sqllocks-shape-eventhubs[schema-registry]") from exc
        if credential is None:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:
                raise ShapeError(
                    "install sqllocks-shape-eventhubs[entra] or give credential"
                ) from exc
            credential = DefaultAzureCredential()
        if schema_text is not None and not group:
            raise ShapeError("Azure registry writes require schema_registry_group")
        client = SchemaRegistryClient(
            fully_qualified_namespace=parts.hostname, credential=credential
        )

        def resolve(schema_id: str) -> str:
            return str(client.get_schema(schema_id).definition)

        encoder = AvroEncoder(client=client, group_name=group, auto_register=True)
    return AzureAvroCodec(encoder, schema_text, resolve=resolve)
