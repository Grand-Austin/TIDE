# Vessel References

Store vessel metadata, model provenance, source links, loading-condition documentation, and reuse terms here. Use one subdirectory per vessel when its reference package is assembled.

S175/MSS is the initial candidate in [Product.md](../../docs/Product.md). Its data have not yet been integrated into the new game runtime. Keep geometry, stability, inertia, and response data consistent with one loading condition.

Download external model files into `data/raw/`. Write generated response tables or converted inputs into `data/processed/`. Reference those files from the vessel metadata and scenario package. Preserve source attribution and review redistribution terms before committing external data.
