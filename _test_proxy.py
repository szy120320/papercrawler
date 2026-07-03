"""Test if 127.0.0.1:10817 is a working HTTP proxy."""
import http.client
import socket

# Connect to proxy and try to fetch a URL through it
proxy_host, proxy_port = "127.0.0.1", 10817
target_host = "github.com"
target_port = 443

print(f"--- Test: Connect to {proxy_host}:{proxy_port} as HTTP proxy ---")
try:
    conn = http.client.HTTPConnection(proxy_host, proxy_port, timeout=10)
    conn.set_tunnel(target_host, target_port)  # creates CONNECT tunnel
    conn.request("GET", "/szy120320")
    r = conn.getresponse()
    body = r.read(200).decode(errors="replace")
    print(f"  Status: {r.status} {r.reason}")
    print(f"  Body[:200]: {body}")
    conn.close()
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
