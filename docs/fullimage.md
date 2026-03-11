# Full Image Build and Transfer

Use the full image when you want a ready-to-run KernelMCP environment, including the VM/image setup from `install.sh`.

## Prerequisites

On the machine that builds the image:

- Docker
- Docker Buildx
- `/dev/kvm`

## Build

Build the Codex full image:

```bash
./buildfullimage.sh
```

This creates:

```bash
kernelmcp-full-<your username>
```

Build the Claude full image:

```bash
./buildfullimage.sh --claude
```

This creates:

```bash
kernelmcp-full-<your username>-claude
```

## Transfer to Another Server

Save the image to a tarball on the source machine:

```bash
docker save kernelmcp-full-<your username> | gzip > kernelmcp-full-<your username>.tar.gz
```

For the Claude variant:

```bash
docker save kernelmcp-full-<your username>-claude | gzip > kernelmcp-full-<your username>-claude.tar.gz
```

Copy the tarball to the destination server:

```bash
scp kernelmcp-full-<your username>.tar.gz <remote-host>:~/
```

Load it on the destination server:

```bash
gunzip -c ~/kernelmcp-full-<your username>.tar.gz | docker load
```

For the Claude variant:

```bash
gunzip -c ~/kernelmcp-full-<your username>-claude.tar.gz | docker load
```

## Verify

Check that Docker imported the image:

```bash
docker image ls | grep kernelmcp-full
```

Quick smoke tests:

```bash
docker run --rm --entrypoint bash kernelmcp-full-<your username> -lc 'codex --version'
docker run --rm --entrypoint bash kernelmcp-full-<your username>-claude -lc 'claude --version'
```

## Notes

- Loading the image on another server does not require rebuilding it.
- After `docker load`, the image keeps the original tag from the source machine, for example `kernelmcp-full-jpu007`.
- To actually run KernelMCP workloads there, the destination server still needs Docker access and `/dev/kvm`.
