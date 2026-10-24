# The state process as an appliance (DESIGN §18.9, §39.11): one static
# Rust binary, one token, one volume. Published for amd64 and arm64 at
# ghcr.io/mausa-ai/neosian:<X.Y.Z> by the release workflow.
# Both bases pin multi-arch index digests; Dependabot moves them.
#
#   docker run -e NEOSIAN_SERVE_TOKEN=... -p 6367:6367 -v state:/data \
#       ghcr.io/mausa-ai/neosian:<X.Y.Z>
#   docker build -t neosian .
#
# FileStore and Postgres share the same wire. TLS terminates at a reverse proxy;
# an unset NEOSIAN_SERVE_TOKEN refuses to start at exit 2.

FROM rust:1.99.0-alpine@sha256:0cce0a5e0e8ba67b455257a3a02a1d99005f382748789d6464460028810f1627 AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY crates/ crates/
COPY assets/ assets/
# Alpine's native musl target links statically on both architectures.
# The image's exact toolchain is the repository pin; the explicit choice
# avoids installing rustfmt and clippy in this build-only stage.
RUN cargo +${RUST_VERSION} build --release --locked \
    && mkdir /data && chown 1000:1000 /data && chmod 700 /data

FROM gcr.io/distroless/static-debian13:nonroot@sha256:d62067879de890a1def9da0e7766b7d20810a5e5555cef8506b752437d7a3e88
COPY --from=builder /app/target/release/neosian /usr/local/bin/neosian
COPY --from=builder --chown=1000:1000 /data /data
COPY LICENSE docs/LICENSE-MUSL crates/neosian/LICENSE-TLS crates/neosian/LICENSE-CONSOLE /usr/share/doc/neosian/
# Keep the previous image's uid so existing named volumes remain writable.
USER 1000:1000
ENV NEOSIAN_INSTALL=container
LABEL ai.mausa.neosian.backends="file,postgres"
VOLUME /data
EXPOSE 6367
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s CMD \
    ["/usr/local/bin/neosian", "serve", "--healthcheck"]
# Bind in the entrypoint so overriding the root or mounts keeps it reachable.
ENTRYPOINT ["/usr/local/bin/neosian", "serve", "--host", "0.0.0.0"]
CMD ["--root", "/data"]
