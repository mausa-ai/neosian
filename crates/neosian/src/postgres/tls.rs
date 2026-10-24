//! Verified Postgres TLS, with an optional PEM root bundle for private servers.

use rustls::pki_types::{CertificateDer, pem::PemObject};
use std::io;
use std::path::Path;
use tokio_postgres_rustls::MakeRustlsConnect;

pub fn connector(extra: Option<&Path>) -> io::Result<MakeRustlsConnect> {
    let mut roots =
        rustls::RootCertStore::from_iter(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    if let Some(path) = extra {
        let invalid =
            || io::Error::other("PGSSLROOTCERT must name a readable PEM certificate bundle");
        let mut count = 0;
        for certificate in CertificateDer::pem_file_iter(path).map_err(|_| invalid())? {
            roots
                .add(certificate.map_err(|_| invalid())?)
                .map_err(|_| invalid())?;
            count += 1;
        }
        if count == 0 {
            return Err(invalid());
        }
    }
    let mut config = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_no_client_auth();
    config.alpn_protocols = vec![b"postgresql".to_vec()];
    Ok(MakeRustlsConnect::new(config))
}
