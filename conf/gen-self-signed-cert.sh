#!/bin/bash

echo "NA
NA
NA
Ephorize
Ephorize Self-Signed Cert
Ephorize
root@localhost" | openssl req -x509 -newkey rsa:2048 -keyout self_signed_server.pem -out self_signed_server.pem -nodes -batch


