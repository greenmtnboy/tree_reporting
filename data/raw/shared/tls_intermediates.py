"""
Missing intermediate certificates for portals that serve an incomplete TLS chain.

Some city servers send only their leaf certificate and omit the intermediate
that signed it.  Browsers paper over this by fetching the intermediate from the
leaf's Authority Information Access (AIA) URL; OpenSSL / requests / certifi do
not, so verification fails with ``unable to get local issuer certificate``.

Rather than ever turning verification off, we keep it ON and supply the missing
intermediate ourselves: ``verify_bundle_for(url)`` returns the path of a CA
bundle made of certifi's roots plus the intermediate(s) registered for that
host, or ``None`` for every other host (requests then uses its normal default).
An intermediate only helps if it chains to a root certifi already trusts, so
this adds no new trust anchor.

The PEM is embedded here, not committed as a ``.pem`` file, because the cloud
sync only ships .py/.preql/.csv/.json files to the worker.

Remove an entry once the host serves its full chain again -- check with
``openssl s_client -connect HOST:443 -servername HOST -showcerts``.
"""

from __future__ import annotations

import os
import tempfile
from urllib.parse import urlsplit

# maps.burlingtonvt.gov (Burlington VT ArcGIS, raw/usbtv): since the leaf was
# reissued on 2026-11-02 the server sends only `*.burlingtonvt.gov` and omits
# its issuer.  AIA: http://crt.sectigo.com/SSL2BUYEMEARSADomainValidationSecureServerCA.crt
#   subject: C=AE, O=SSL2BUY EMEA LLC, CN=SSL2BUY EMEA RSA Domain Validation Secure Server CA
#   issuer:  C=GB, O=Sectigo Limited, CN=Sectigo Public Server Authentication Root R46
#   valid:   2024-07-31 .. 2034-07-30
#   sha256:  58:F0:F7:56:75:8E:93:FB:0B:6B:17:A3:6A:38:50:47:5D:68:BC:0D:6C:99:CB:E2:2A:1B:18:35:1C:89:FF:1F
SSL2BUY_EMEA_RSA_DV_CA = """\
-----BEGIN CERTIFICATE-----
MIIGSzCCBDOgAwIBAgIRAJvUQHUeRiPp62D8LGqVNMcwDQYJKoZIhvcNAQEMBQAw
XzELMAkGA1UEBhMCR0IxGDAWBgNVBAoTD1NlY3RpZ28gTGltaXRlZDE2MDQGA1UE
AxMtU2VjdGlnbyBQdWJsaWMgU2VydmVyIEF1dGhlbnRpY2F0aW9uIFJvb3QgUjQ2
MB4XDTI0MDczMTAwMDAwMFoXDTM0MDczMDIzNTk1OVowZjELMAkGA1UEBhMCQUUx
GTAXBgNVBAoTEFNTTDJCVVkgRU1FQSBMTEMxPDA6BgNVBAMTM1NTTDJCVVkgRU1F
QSBSU0EgRG9tYWluIFZhbGlkYXRpb24gU2VjdXJlIFNlcnZlciBDQTCCAaIwDQYJ
KoZIhvcNAQEBBQADggGPADCCAYoCggGBALsbge3dwtQ6DNyJOcsZ+ZNJw/wpFgoW
xtz5f07UlYcc5ob1YU5hUbwTdfCHl3p2NiIjYedo2jb0qmgD8x0fycJ4yNG0hQRy
ptIUSkzyCEfG5QbNLK8osOPq/b274qVYuh7FeGk08JO050i7yJdUyYT3FlrTr/BA
hy2ckfwu27j63NVWzmT+piryjtCa2md1Txpmn0P2JedZKdzO3nopLYrvQ7ulxh0k
kZFG0QJbTwIC2miuExxTsLfr88dy2OiArQ7mts4aJJTWGxKGP14qTQiv9La5GUwV
y1j4tSxMVJtrQyhdXrAqydo7Z/CP8Crw/5aUlK1DRu7kTQ36S2CJD1M65h7sYaZc
TuXUGcgmeQXBeVsUYPQB7jsrkgaZLEwliyrWF8YUJcYfYHzKqb5TVuMljDsKLweI
1dowbB6B21QpWHwf0n2kCJPR0R2daKClSR9cVi/WyyMBy7zBnMVW6XvfQqH6qrs3
7oGjZhS+XmzgIJIrW9+G46zhw02QiyclLQIDAQABo4IBeTCCAXUwHwYDVR0jBBgw
FoAUVnNYZJX5khqwEioEYnmhQBWIIUkwHQYDVR0OBBYEFIFZSMm65QBv37f/UMWr
793m5K6QMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0GA1Ud
JQQWMBQGCCsGAQUFBwMBBggrBgEFBQcDAjATBgNVHSAEDDAKMAgGBmeBDAECATBU
BgNVHR8ETTBLMEmgR6BFhkNodHRwOi8vY3JsLnNlY3RpZ28uY29tL1NlY3RpZ29Q
dWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYuY3JsMIGEBggrBgEFBQcB
AQR4MHYwTwYIKwYBBQUHMAKGQ2h0dHA6Ly9jcnQuc2VjdGlnby5jb20vU2VjdGln
b1B1YmxpY1NlcnZlckF1dGhlbnRpY2F0aW9uUm9vdFI0Ni5wN2MwIwYIKwYBBQUH
MAGGF2h0dHA6Ly9vY3NwLnNlY3RpZ28uY29tMA0GCSqGSIb3DQEBDAUAA4ICAQAI
Lv4hSPNBcCiZPejKD1/xCJsZjLEHiQDEMCABEQ3wzmdKlCGxeKBhyi46URu0BKLY
SoDHT/qXa1QOxt8lUYn1LkGwiN2jWgUn8UK3VloGxmHgKQ9OuAH893TyQNktvZLr
35Ve9qREIBlBNtfNlwqBsm2Z3Vyzd1r+iZJioqG63dSx29Tm9YUcRPzkFXEsrWvp
3uTpSWWnSEo7FPEKk66RuCy1qDViOuAB5DOAWdM/dB5OL52UxisLf91EvZfZHOu2
G5KVA45DvXi7Jmgq/9zL9hM74ziNAudWjruOE/cPfPr2HbW5MTXcmcMctSXy93Df
RqIXoMVHj5ZrZRpxO6vvxNm6wxyizkqOg6WndgTFa13o0Tmva6fVzqVsdm06lNT1
dQA8tu7c+fdWw98ZIUSNeR9S0b/Rzjl0nr0mITVliDwJJhfsPjijLaCLCXBQqA1h
nYTWaGt1HYWEVK0L0SpZRqPcBa15QMHX3Y6wd3nNcRrU2OkgMHox0pKnuhbsVblM
wf5onD0m/CIPJ2pIEKn60Pv8oAXVk19hwHA8IhzSZi8u9atuNJC5vjDj2Uo9bUW1
UJj7MuKhTihTxYlaib8dh1Oq6k9S6AISeQJFgz/eEYPqyTm+irLaFMM/S6eg9M84
J/LdTC0VaJ+lSR+7k+6h3usVo1OXybt5lV9hC+Ymqg==
-----END CERTIFICATE-----
"""

MISSING_INTERMEDIATES: dict[str, tuple[str, ...]] = {
    "maps.burlingtonvt.gov": (SSL2BUY_EMEA_RSA_DV_CA,),
}

_bundles: dict[str, str] = {}


def verify_bundle_for(url: str) -> str | None:
    """CA bundle path (certifi + the host's missing intermediates), or None."""
    host = (urlsplit(url).hostname or "").lower()
    extra = MISSING_INTERMEDIATES.get(host)
    if not extra:
        return None
    path = _bundles.get(host)
    if path and os.path.exists(path):
        return path
    import certifi

    with open(certifi.where(), encoding="ascii") as f:
        roots = f.read()
    fd, path = tempfile.mkstemp(prefix=f"ca-{host}-", suffix=".pem")
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(roots.rstrip("\n") + "\n" + "\n".join(p.strip() for p in extra) + "\n")
    _bundles[host] = path
    return path
