import select
import socket
import socketserver

class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(("127.0.0.1", 11434), timeout=10) as upstream:
                upstream.settimeout(1200)
                self.request.settimeout(1200)
                peers = {self.request: upstream, upstream: self.request}
                while True:
                    ready, _, _ = select.select(list(peers), [], [], 1200)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        peers[source].sendall(data)
        except OSError as exc:
            print("bridge_connection_failed", type(exc).__name__, flush=True)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

with Server(("172.19.0.1", 11435), Relay) as server:
    print("ollama_bridge_ready", flush=True)
    server.serve_forever()
