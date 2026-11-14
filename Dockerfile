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

FROM rust:1.99.1-alpine@sha256:44819b27be4c97b0e4d7203bd2e02c4820f97700211b6a37fb4c246a2de59383 AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY crates/ crates/
COPY assets/ assets/
# Alpine's native musl target links statically on both architectures.
# The image's exact toolchain is the repository pin; the explicit choice
# avoids installing rustfmt and clippy in this build-only stage.
RUN cargo +${RUST_VERSION} build --release --locked \
    && mkdir /data && chown 1000:1000 /data && chmod 700 /data

FROM gcr.io/distroless/static-debian13:nonroot@sha256:aafe993665bf6200db38a02c11a9351e782a84b6a45e7f33b6695638c88e3931
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
