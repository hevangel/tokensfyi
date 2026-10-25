# OCI runtime images

The root `Dockerfile` is the single pinned application image used for web and worker roles. Compose supplies role-specific commands so local and AWS deployments execute the same Python package and lockfile. Production builds must be promoted by immutable image digest in CI.
