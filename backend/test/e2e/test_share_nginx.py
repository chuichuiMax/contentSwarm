"""Exercise the shipped Nginx routes and TLS entry with the real share renderer."""

import io
import os
import tarfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from PIL import Image

pytestmark = pytest.mark.e2e
NGINX_IMAGE = "nginx:1.31.2-alpine@sha256:35cd77497979abe70dc8d26f5ae60811eea233a2eb5dc03c2ee30972caeb303e"
SOURCE_ROOT = Path(os.getenv("SHARE_NGINX_SOURCE_ROOT", Path(__file__).resolve().parents[3]))

# Only persistence is replaced: public URL selection and HTML come from application code.
UPSTREAM = """
import io, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from fastapi import Request
from PIL import Image
from server.utils.public_url import request_public_base_url
from yuxi.services.material_library_service import render_public_material_share_page

share = SimpleNamespace(
    token='test-token', title='Share test', building_name='Building', area='120', design_style='Style')
items = [SimpleNamespace(display_order=i) for i in (1, 2)]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        scope = {'type':'http', 'scheme':'http', 'server':('api',5050), 'path':self.path,
                 'root_path':'', 'query_string':b'',
                 'headers':[(k.lower().encode(),v.encode()) for k,v in self.headers.items()]}
        base = request_public_base_url(Request(scope))
        if self.path == '/share/case/test-token' or self.path.endswith('/test-token/page'):
            data = render_public_material_share_page(share, items, base).encode()
            content_type = 'text/html; charset=utf-8'
        elif self.path.endswith('.webp') or self.path.endswith('/cover.jpg'):
            fmt = 'WEBP' if self.path.endswith('.webp') else 'JPEG'
            image = Image.new('RGB', (500,400) if fmt == 'JPEG' else (48,36), 'blue')
            buffer = io.BytesIO(); image.save(buffer, format=fmt); data = buffer.getvalue()
            content_type = 'image/webp' if fmt == 'WEBP' else 'image/jpeg'
        else:
            self.send_error(404); return
        self.send_response(200); self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        scope = {'type':'http','scheme':'http','server':('api',5050),'path':self.path,'root_path':'',
                 'query_string':b'','headers':[(k.lower().encode(),v.encode()) for k,v in self.headers.items()]}
        base = request_public_base_url(Request(scope))
        data = json.dumps({'page_url':base+'/share/case/test-token','path':self.path}).encode()
        self.send_response(201); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
ThreadingHTTPServer(('0.0.0.0',5050),Handler).serve_forever()
"""


class ShareTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.images.append(attrs["src"])
        if tag == "meta" and attrs.get("property") in ("og:image", "og:url"):
            self.metadata[attrs["property"]] = attrs["content"]


def put_files(docker_api, container_id, files):
    """Copy isolated test configuration without changing repository or running services."""
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, data in files.items():
            if isinstance(data, str):
                data = data.encode()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    response = docker_api.put(f"/containers/{container_id}/archive", params={"path": "/"}, content=archive.getvalue())
    response.raise_for_status()


@pytest.fixture(scope="module")
def nginx_origins():
    client = httpx.Client(
        transport=httpx.HTTPTransport(uds="/var/run/docker.sock"), base_url="http://docker", timeout=60
    )
    api_image = os.getenv("SHARE_NGINX_API_IMAGE") or client.get("/containers/api-dev/json").json()["Image"]
    response = client.post("/networks/create", json={"Name": f"share-proxy-test-{uuid.uuid4().hex[:10]}"})
    response.raise_for_status()
    network_id = response.json()["Id"]
    containers = []
    try:
        response = client.post(
            "/containers/create",
            json={
                "Image": api_image,
                "Cmd": ["python", "/tmp/share_upstream.py"],
                "Env": ["YUXI_SKIP_APP_INIT=1", "PYTHONPATH=/app:/app/package"],
                "HostConfig": {"NetworkMode": network_id},
                "NetworkingConfig": {"EndpointsConfig": {network_id: {"Aliases": ["api", "minio", "hycanvas-app"]}}},
            },
        )
        response.raise_for_status()
        upstream = response.json()["Id"]
        containers.append(upstream)
        put_files(client, upstream, {"tmp/share_upstream.py": UPSTREAM})
        client.post(f"/containers/{upstream}/start").raise_for_status()

        # A private certificate exercises the real TLS-terminating host template.
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "share.example.test")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        response = client.post(
            "/containers/create",
            json={
                "Image": NGINX_IMAGE,
                "ExposedPorts": {"80/tcp": {}, "443/tcp": {}},
                "HostConfig": {
                    "NetworkMode": network_id,
                    "PortBindings": {port: [{"HostIp": "0.0.0.0", "HostPort": ""}] for port in ("80/tcp", "443/tcp")},
                },
            },
        )
        response.raise_for_status()
        nginx = response.json()["Id"]
        containers.append(nginx)
        default = (SOURCE_ROOT / "docker/nginx/default.conf").read_text(encoding="utf-8")
        put_files(
            client,
            nginx,
            {
                "etc/nginx/nginx.conf": (SOURCE_ROOT / "docker/nginx/nginx.conf").read_bytes(),
                # The outer template's 8090 upstream stays inside this isolated container.
                "etc/nginx/conf.d/default.conf": default.replace("listen 80;", "listen 80;\n    listen 8090;"),
                "etc/nginx/yuxi-boyun.conf": (SOURCE_ROOT / "scripts/nginx/yuxi-boyun.conf").read_bytes(),
                "etc/nginx/yuxi-root-public-origin.conf": (
                    SOURCE_ROOT / "scripts/nginx/yuxi-root-public-origin.conf"
                ).read_bytes(),
                "etc/nginx/conf.d/share-test-edge.conf": """server {
                    listen 443 ssl;
                    server_name share.example.test;
                    ssl_certificate /etc/nginx/share-test.crt;
                    ssl_certificate_key /etc/nginx/share-test.key;
                    include /etc/nginx/yuxi-boyun.conf;
                    location ^~ /share/case/ {
                        proxy_pass http://127.0.0.1:8090;
                        include /etc/nginx/yuxi-root-public-origin.conf;
                    }
                    location ^~ /api/ {
                        proxy_pass http://127.0.0.1:8090;
                        include /etc/nginx/yuxi-root-public-origin.conf;
                    }
                }""",
                "etc/nginx/share-test.crt": cert.public_bytes(serialization.Encoding.PEM),
                "etc/nginx/share-test.key": key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                ),
            },
        )
        client.post(f"/containers/{nginx}/start").raise_for_status()
        host = os.getenv("SHARE_NGINX_DOCKER_HOST", "127.0.0.1")
        ports = client.get(f"/containers/{nginx}/json").json()["NetworkSettings"]["Ports"]
        origins = {
            "http": f"http://{host}:{ports['80/tcp'][0]['HostPort']}",
            "https": f"https://{host}:{ports['443/tcp'][0]['HostPort']}",
        }
        with httpx.Client(verify=False, trust_env=False, timeout=3) as probe:
            for _ in range(60):
                try:
                    # This route exists even before the sharing route is fixed.
                    response = probe.get(f"{origins['http']}/api/material-library/shares/test-token/page")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                logs = [client.get(f"/containers/{c}/logs", params={"stdout": 1, "stderr": 1}).text for c in containers]
                pytest.fail(f"Proxy not ready: {logs}")
        response = client.post(
            f"/containers/{nginx}/exec",
            json={
                "AttachStdout": True,
                "AttachStderr": True,
                "Cmd": ["nginx", "-t"],
            },
        )
        response.raise_for_status()
        exec_id = response.json()["Id"]
        syntax = client.post(f"/exec/{exec_id}/start", json={"Detach": False, "Tty": False})
        syntax.raise_for_status()
        assert client.get(f"/exec/{exec_id}/json").json()["ExitCode"] == 0, syntax.text
        yield origins
    finally:
        for container in reversed(containers):
            client.delete(f"/containers/{container}", params={"force": True}).raise_for_status()
        client.delete(f"/networks/{network_id}").raise_for_status()
        client.close()


@pytest.mark.parametrize(
    ("path", "headers", "public_base"),
    [
        ("/share/case/test-token", {"Host": "share.example.test"}, "http://share.example.test"),
        (
            "/share/case/test-token",
            {"Host": "internal.test", "X-Forwarded-Host": "share.example.test:8443", "X-Forwarded-Proto": "https"},
            "https://share.example.test:8443",
        ),
        (
            "/boyun/share/case/test-token",
            {"Host": "share.example.test", "X-Forwarded-Proto": "https"},
            "https://share.example.test/boyun",
        ),
        (
            "/share/case/test-token",
            {"Host": "share.example.test", "X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/boyun"},
            "https://share.example.test/boyun",
        ),
    ],
)
def test_production_share_page_and_all_images(nginx_origins, path, headers, public_base):
    with httpx.Client(base_url=nginx_origins["http"], trust_env=False, headers=headers) as client:
        response = client.get(path)
        assert response.status_code == 200, response.text
        tags = ShareTags()
        tags.feed(response.text)
        assert len(tags.images) == 3, response.text
        assert tags.metadata["og:url"] == f"{public_base}/share/case/test-token"
        assert tags.metadata["og:image"] == f"{public_base}/api/material-library/shares/test-token/cover.jpg"
        for url in tags.images + [tags.metadata["og:image"]]:
            assert url.startswith(f"{public_base}/api/material-library/shares/test-token/"), url
            image = client.get(urlsplit(url).path)
            assert image.status_code == 200, image.text
            assert image.headers["content-type"] == ("image/jpeg" if url.endswith("cover.jpg") else "image/webp")
            with Image.open(io.BytesIO(image.content)) as decoded:
                decoded.load()
                assert decoded.format == ("JPEG" if url.endswith("cover.jpg") else "WEBP")
        creation_path = "/boyun/api/mp/share/cases" if path.startswith("/boyun/") else "/api/mp/share/cases"
        created = client.post(creation_path, json={"item_ids": ["image-1"]})
        assert created.status_code == 201
        assert created.json()["page_url"] == f"{public_base}/share/case/test-token"


@pytest.mark.parametrize("prefix", ["", "/boyun"])
@pytest.mark.parametrize("spoofed_forwarding", [False, True], ids=["normal-client", "spoofed-headers"])
def test_tls_host_template_preserves_prefix_and_overwrites_client_forwarding(nginx_origins, prefix, spoofed_forwarding):
    headers = {"Host": "share.example.test"}
    if spoofed_forwarding:
        headers.update({"X-Forwarded-Host": "api:5050", "X-Forwarded-Proto": "http", "X-Forwarded-Prefix": "/wrong"})
    with httpx.Client(base_url=nginx_origins["https"], verify=False, trust_env=False, headers=headers) as client:
        response = client.get(f"{prefix}/share/case/test-token")
        assert response.status_code == 200, response.text
        tags = ShareTags()
        tags.feed(response.text)
        base = f"https://share.example.test{prefix}"
        assert tags.metadata["og:url"] == f"{base}/share/case/test-token"
        assert len(tags.images) == 3
        for url in tags.images + [tags.metadata["og:image"]]:
            assert url.startswith(f"{base}/api/material-library/shares/test-token/"), url
            image = client.get(urlsplit(url).path)
            assert image.status_code == 200, image.text
            with Image.open(io.BytesIO(image.content)) as decoded:
                decoded.load()
        created = client.post(f"{prefix}/api/mp/share/cases", json={"item_ids": ["image-1"]})
        assert created.status_code == 201
        assert created.json() == {"page_url": f"{base}/share/case/test-token", "path": "/api/mp/share/cases"}
