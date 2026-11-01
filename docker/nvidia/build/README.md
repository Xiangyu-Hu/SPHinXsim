# SPHinXsim NVIDIA 标准镜像构建

本目录提供 RTX 3090 和 NVIDIA H20 的 SPHinXsim 容器构建文件，每种 GPU 分别支持
CUDA 12.3 和 CUDA 13。构建后的镜像包含 SPHinXsim、Python 环境、C++/SYCL 编译器及运行库，
可以直接读取 JSON 配置并运行计算算例。

**每个目标使用独立 Dockerfile。** GPU 架构已固定在文件中，构建时无需传入 `CUDA_ARCH`。
本文按“选择目标 → 准备源码和依赖 → 构建 → 验证 → 运行”的顺序说明操作。
本目录可以单独压缩分发，无需同时打包整个 SPHinXsim 仓库。
接收者准备完整 SPHinXsim 源码（Git clone 或源码 ZIP 解压目录均可）和构建环境，即可使用。
所有命令使用 Bash，默认镜像名为本地标签 `sphinxsim:*`，无需登录镜像仓库。

## 快速开始：收到构建包后如何使用

假设你将构建包解压到 `$HOME/sphinxsim-docker-build`，源码解压到 `$HOME/SPHinXsim`。
源码根目录应直接包含 `CMakeLists.txt` 和 `sphinxsim/`，且包含 `sphinxsim/sphinxsys/` 依赖源码。
Linux x86_64 主机已安装 Docker、Python 3.10+ 后：

```bash
cd "$HOME/sphinxsim-docker-build"

# 例如：构建 RTX 3090 + CUDA 13；最后一个参数必须是尚不存在的新目录。
sudo bash build-from-source.sh 3090-cuda13 \
  "$HOME/SPHinXsim" "$HOME/sphinxsim-build-context"
```

脚本会依次复制源码、准备依赖、校验安装包、调用对应 Dockerfile，最终生成
`sphinxsim:3090-cuda13`。需要 H20 时把第一个参数改为 `h20-cuda13`；
需要 CUDA 12.3 时选择 `3090-cuda12` 或 `h20-cuda12`。完整对应关系见下表。
构建时需要联网，下载量和软件源见第 4 节。

如需调整资源或使用已有安装包缓存：

```bash
sudo env BUILD_JOBS=4 BUILD_CPUS=4 BUILD_MEMORY=16g \
  PACKAGE_CACHE="$HOME/sphinxsim-package-cache" \
  IMAGE_TAG=sphinxsim:h20-cuda13-custom \
  bash build-from-source.sh h20-cuda13 \
  "$HOME/SPHinXsim" "$HOME/sphinxsim-h20-build-context"
```

一键脚本接受可选环境变量：`PACKAGE_CACHE`（归档缓存目录）、`DOWNLOAD_PROXY`（归档下载代理）、
`ALLOW_LARGE_PROXY=1`（确认允许通过代理下载超过 1 GB 的单文件）、
`DOWNLOAD_DEPENDENCIES=0`（仅用本地缓存）以及 `SOURCE_REVISION`（可选 Git 提交）。
不设置 `SOURCE_REVISION` 时无需安装 Git，直接使用提供的源码文件，包括其中的本地修改。

准备成功但下载或构建失败时，已创建的上下文会保留。进入该目录，按第 4、5 节重试相应步骤；
不要把已存在目录再次传给一键脚本。

## 1. 选择构建目标

| 构建目标 | Dockerfile | 目标 GPU 架构 | CUDA Toolkit | Intel oneAPI | 本文使用的镜像名 |
|---|---|---|---|---|---|
| `3090-cuda12` | [3090-cuda12/Dockerfile](3090-cuda12/Dockerfile) | RTX 3090 / `sm_86` | 12.3.1 | 2025.2 | `sphinxsim:3090` |
| `h20-cuda12` | [h20-cuda12/Dockerfile](h20-cuda12/Dockerfile) | H20 / `sm_90` | 12.3.1 | 2025.2 | `sphinxsim:h20` |
| `3090-cuda13` | [3090-cuda13/Dockerfile](3090-cuda13/Dockerfile) | RTX 3090 / `sm_86` | 13.0.3 | 2026.1.1 | `sphinxsim:3090-cuda13` |
| `h20-cuda13` | [h20-cuda13/Dockerfile](h20-cuda13/Dockerfile) | H20 / `sm_90` | 13.0.3 | 2026.1.1 | `sphinxsim:h20-cuda13` |

四种环境均基于 Ubuntu 22.04，使用 Boost 1.85.0、CMake Release（`-O3 -DNDEBUG`）、
单精度和 SYCL NVIDIA 后端。请按**运行机器的 GPU**选择目标：构建 H20 镜像的机器本身可以没有 H20。
其他 GPU 型号需核实计算能力，并另行调整、构建和验证对应 Dockerfile；更改镜像标签不会改变设备代码架构。

两套工具链的主要区别：

- CUDA 12.3：安装 oneAPI 2025.2 的编译器等组件，并通过 Codeplay 2025.2 安装 NVIDIA adapter。
- CUDA 13：使用官方 oneAPI 2026.1.1 编译器、头文件、SYCL runtime 和 UR loader，
  仅从 `intel/llvm v7.1.0` 提取 `libur_adapter_cuda.so*`，不安装旧 Codeplay 安装包。

两者均通过 `SPHinXsim → SYCL → Unified Runtime → CUDA adapter → NVIDIA 驱动` 执行 GPU 计算。
更换工具链不保证所有算例都更快，实际性能和数值行为需用目标算例验证。

## 2. 环境要求

### 构建机器

- Linux x86_64，以及可访问的 Docker daemon。
- Python 3.10+ 和 Bash。默认复制源码不需要 Git；仅使用 `--revision` 导出 Git 提交时需要 Git 和 tar。
- 本文的 `build.sh` 使用 Docker classic builder（`DOCKER_BUILDKIT=0`）；需要 Docker 仍提供该功能。
  使用仅支持 BuildKit 的环境时，参考后面的直接构建方式。
- 默认构建配置为 8 个编译任务、8 CPU 配额和 24 GiB 容器内存上限；主机还应为操作系统及其他服务预留资源。
- 单个最终镜像约 12–13 GB。源码、安装包和中间层会额外占用磁盘，建议至少预留 50 GB，
  构建全部目标或导出镜像时需要更多空间。
- 网络需能访问 NVIDIA CUDA 基础镜像、Ubuntu/Python 软件源及所选工具链的下载源。

构建阶段不需要 GPU、NVIDIA Container Toolkit 或宿主机 oneAPI。GPU 验证在运行机器上完成。
下面的 Docker 命令使用 `sudo`；如果当前用户已获得 Docker 访问权限，可以省略它。

### 运行机器

- Linux x86_64、与镜像匹配的 NVIDIA GPU。
- 支持对应 CUDA Toolkit 的 NVIDIA 驱动。CUDA 13 需要 R580 或更新且支持目标 GPU 的驱动；
  CUDA 12.3 也需满足 NVIDIA 对该版本的驱动兼容要求。
- Docker 和已配置 Docker GPU 支持的
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

宿主机无需另装 CUDA Toolkit 或 oneAPI。`nvidia-smi` 显示的 CUDA 版本代表驱动支持能力，
并不等于容器内的 CUDA Toolkit 版本。

## 3. 准备构建上下文

Dockerfile 的 `COPY` 路径相对于**构建上下文根目录**，不是相对于 Dockerfile 所在目录。
请先通过脚本生成上下文，不要直接在 `3090-cuda13/` 等子目录执行 `docker build .`。

除快速开始的一键脚本外，也可以分步操作，便于检查源码、依赖和构建参数。
从**解压后的构建包目录**执行，不要求构建包位于源码仓库内：

```bash
cd "$HOME/sphinxsim-docker-build"
BUILD_CONTEXT="$HOME/sphinxsim-build-context"
python3 prepare-context.py "$BUILD_CONTEXT" --source "$HOME/SPHinXsim"
cd "$BUILD_CONTEXT"
```

`--source` 必须指向 SPHinXsim 源码根目录，可以是 Git 工作目录，也可以是无 `.git` 的源码 ZIP 解压目录。
目标上下文必须位于源码目录和构建包目录之外，且尚不存在，脚本不会覆盖已有目录。
构建脚本、Dockerfile 和入口文件取自当前构建包。第三方安装器不随源码分发；归档依赖另行获取并校验。

默认复制所提供的源码，保留本地代码和算例修改；排除 `.git`、虚拟环境、常见编译/缓存目录、
`.env*`、编译产物及压缩归档。根目录的 `docker/`、`output/`、`results/`、`restart/`、`reload/`
也不复制；符号链接必须为指向导出树内实际文件的相对链接。
如果项目确实依赖这些被排除的文件，应在构建前检查快照并调整过滤规则。
脚本会对导出文件生成哈希清单，镜像源码标识采用 `source-<内容摘要>`，不伪称对应某个 Git 提交。

如需严格构建已提交版本，在 Git 源码目录上显式使用 `--revision`：

```bash
# 使用另一个新目录；--revision 模式忽略未提交修改。
python3 prepare-context.py "$HOME/sphinxsim-release-context" \
  --source "$HOME/SPHinXsim" --revision HEAD
```

此模式也接受提交 ID 或 tag，并将实际提交写入镜像来源信息；源码中存在 Git submodule 时，
请先初始化完整子模块，再使用默认复制模式。此前测试的源码提交为
`7b737a24f3fa26e006b7029e137ae4f23fa3cfbf`，脚本不强制固定它。
对于更新或自行修改的源码版本，需重新验证工具链兼容性。

生成后的目录结构如下：

```text
sphinxsim-build-context/            # docker build 最后的路径参数
├── source/                        # 复制或导出的 SPHinXsim 源码
├── source-revision.txt            # 内容标识，或显式选择的 Git 提交 ID
├── source-manifest.json           # 源码导出信息
├── downloads/                     # 下一步准备的安装包
├── .dockerignore
└── build/
    ├── 3090-cuda12/Dockerfile
    ├── h20-cuda12/Dockerfile
    ├── 3090-cuda13/Dockerfile
    ├── h20-cuda13/Dockerfile
    ├── build-from-source.sh       # 从任意源码目录开始的一键入口
    ├── build.sh                   # 构建已准备的单个目标
    ├── build-matrix.sh            # 顺序构建全部四个目标
    ├── prepare-context.py
    ├── dependencies.py
    ├── dependencies.lock.json
    ├── context.dockerignore
    ├── packages/                  # 依赖获取说明及原始许可证
    └── runtime/                   # 容器入口、算例运行器及 GPU 探针
```

也可以把生成的整个目录复制到另一台 Linux x86_64 Docker 主机，然后在该目录继续操作。
源码准备完成后，后续构建不再依赖原始 Git 工作目录。

## 4. 准备依赖安装包

以下命令均在生成的构建上下文根目录执行。
只需准备选中 CUDA 版本的依赖，不必下载另一版本的安装包。

### CUDA 13

```bash
python3 build/dependencies.py cuda13 --download
python3 build/dependencies.py cuda13 --check
```

这会下载 Boost 和 DPC++ 发布归档并校验大小、SHA-256。构建时再从 Intel 签名 APT 源安装
`intel-oneapi-compiler-dpcpp-cpp-2026.1=2026.1.1-325`。

### CUDA 12.3

Codeplay 2025.2 安装器不随源码分发，且锁文件没有可用的固定公开下载地址。
请先按 [`packages/README.md`](packages/README.md) 从供应商取得精确版本，放到自己的缓存目录：

```bash
python3 build/dependencies.py cuda12 --download --cache "$HOME/sphinxsim-package-cache"
python3 build/dependencies.py cuda12 --check
```

脚本校验并复制用户提供的 Codeplay 安装器，下载缺少的 Intel 离线包和 Boost 归档。
Intel 离线包约 2.46 GB，构建时只安装指定的编译器、DPL 和 TBB 组件。
依赖来源、版权和许可说明见 [`packages/README.md`](packages/README.md)。

### 复用已有安装包

如果已经下载了所需文件，可完全跳过归档下载：

```bash
# 单个版本用 cuda12 或 cuda13；准备全部四个镜像用 all。
python3 build/dependencies.py all --cache "$HOME/sphinxsim-package-cache"
python3 build/dependencies.py all --check
```

脚本按顺序查找 `--cache` 指定的目录，不自动扫描用户文件。
`--cache` 可以重复传入多个目录，文件名必须与下表一致。
也可以直接把文件放入上下文的 `downloads/`，再执行 `--check`。
现有文件大小或哈希不匹配时脚本会中止，不会自动覆盖。

| 文件名 | 精确字节数 | 使用版本 |
|---|---:|---|
| `boost_1_85_0.tar.bz2` | 124,015,250 | 两版共用 |
| `intel-oneapi-base-toolkit-2025.2.0.592_offline.sh` | 2,462,580,605 | CUDA 12.3 |
| `oneapi-for-nvidia-gpus-2025.2.0-linux.sh` | 318,643 | CUDA 12.3 |
| `sycl-v7.1.0-linux.tar.gz` | 217,981,850 | CUDA 13 |

具体 URL 和 SHA-256 见 [`dependencies.lock.json`](dependencies.lock.json)。
归档准备完成不等于整个构建可离线进行：基础镜像、APT 和 pip 依赖仍需网络或相应缓存。

### 软件源与代理

Dockerfile 默认使用中科大 Ubuntu 和 PyPI 镜像，Intel APT 使用官方签名仓库。
需要其他镜像源时，在选中 Dockerfile 的 APT/pip 安装命令中替换对应 URL。

依赖下载脚本不自动继承系统代理，需显式指定：

```bash
python3 build/dependencies.py cuda13 --download \
  --proxy http://proxy.example.com:7890
```

将代理地址替换为自己可用的地址。脚本会先显示锁定的下载大小，默认拒绝通过代理下载
超过 1 GB（十亿字节）的单个文件；确认允许该流量后，追加 `--allow-large-proxy`。
代理不会写入最终镜像。

这个参数只控制 `dependencies.py` 的归档下载，不控制 Docker 拉取基础镜像或构建中的 APT/pip。
后两者需要分别配置 Docker daemon 或构建网络的代理。

## 5. 构建镜像

### 方式一：使用构建脚本

在构建上下文根目录中，选择需要的命令执行即可：

```bash
# RTX 3090 + CUDA 12.3 → sphinxsim:3090
sudo env IMAGE_PREFIX=sphinxsim bash build/build.sh 3090-cuda12

# H20 + CUDA 12.3 → sphinxsim:h20
sudo env IMAGE_PREFIX=sphinxsim bash build/build.sh h20-cuda12

# RTX 3090 + CUDA 13 → sphinxsim:3090-cuda13
sudo env IMAGE_PREFIX=sphinxsim bash build/build.sh 3090-cuda13

# H20 + CUDA 13 → sphinxsim:h20-cuda13
sudo env IMAGE_PREFIX=sphinxsim bash build/build.sh h20-cuda13
```

脚本默认使用本地前缀 `sphinxsim`，示例中显式设置同样的值；也可通过该参数改为自己的镜像仓库路径。
构建成功后脚本输出 `Built <镜像名>`，并在 `logs/` 保存构建日志和镜像检查信息。
每个 Dockerfile 都包含完整构建阶段，无需预先制作自定义系统镜像。

需要全部四个目标时，先准备 `all` 依赖，再顺序构建：

```bash
sudo env IMAGE_PREFIX=sphinxsim bash build/build-matrix.sh
```

单个目标可调整资源和完整镜像名，例如：

```bash
sudo env BUILD_JOBS=4 BUILD_CPUS=4 BUILD_MEMORY=16g \
  IMAGE_TAG=sphinxsim:h20-cuda13-custom \
  bash build/build.sh h20-cuda13
```

| 参数 | 默认值 | 作用 |
|---|---|---|
| `BUILD_JOBS` | `8` | SPHinXsim 的 CMake/Ninja 并行编译任务数 |
| `BUILD_CPUS` | `8` | 构建容器的 CPU 配额 |
| `BUILD_MEMORY` | `24g` | 构建容器的内存上限 |
| `IMAGE_PREFIX` | `sphinxsim` | 统一设置镜像名前缀，可改为自己的仓库路径 |
| `IMAGE_TAG` | 按目标生成 | 单目标的完整镜像名，优先于 `IMAGE_PREFIX` |

编译并行数和 CPU 配额必须是正整数。减小并行数通常可降低编译内存需求，但所需内存取决于源码和工具链。
批量脚本禁止设置单个 `IMAGE_TAG`，以免四个目标覆盖同一标签。
再次构建到相同标签会更新其指向；需要保留多个版本时请使用不同标签。

### 方式二：直接使用 Dockerfile

不使用封装脚本时，仍需先完成源码导出和依赖准备。
下面以 RTX 3090 + CUDA 13 为例，在**构建上下文根目录**执行：

```bash
python3 build/dependencies.py cuda13 --check

sudo env DOCKER_BUILDKIT=0 docker build \
  --network host --memory 24g --cpu-period 100000 --cpu-quota 800000 \
  --build-arg BUILD_JOBS=8 \
  --build-arg SOURCE_REVISION="$(cat source-revision.txt)" \
  -f build/3090-cuda13/Dockerfile \
  -t sphinxsim:3090-cuda13 \
  .
```

最后的 `.` 是包含 `source/`、`downloads/` 和 `build/` 的上下文目录，不能省略。
`-f` 选择 Dockerfile，`-t` 指定生成的镜像名。要构建其他目标，按第 1 节表格替换
`-f` 和 `-t`，并将依赖检查的版本改为 `cuda12` 或 `cuda13`。

如果环境使用 BuildKit/buildx，可在已配置相应 builder 的前提下使用：

```bash
sudo docker buildx build --platform linux/amd64 --load \
  --build-arg BUILD_JOBS=8 \
  --build-arg SOURCE_REVISION="$(cat source-revision.txt)" \
  -f build/3090-cuda13/Dockerfile \
  -t sphinxsim:3090-cuda13 \
  .
```

`--load` 将单平台构建结果加载到本地 Docker。此命令没有设置经典 builder 的 CPU/内存配额，
应在 BuildKit builder 层配置资源。建议在原生 Linux x86_64 主机上构建；ARM 上的模拟构建未验证。

## 6. 检查构建结果

先选择刚构建的镜像，例如：

```bash
IMAGE=sphinxsim:3090-cuda13
sudo docker image inspect "$IMAGE" --format '{{.Id}} {{json .Config.Labels}}'
sudo docker run --rm "$IMAGE" --help
sudo docker run --rm "$IMAGE" --exec nvcc --version
sudo docker run --rm "$IMAGE" --exec icpx --version
```

Labels 中的 `sphinxsim.cuda.arch` 应与目标一致，`org.opencontainers.image.revision` 应与
`source-revision.txt` 一致（默认是内容标识，`--revision` 模式下是 Git 提交）。镜像内 `/opt/SPHinXsim/build-info/` 保存源码来源、CMake 配置以及
APT/Python 包清单；CUDA 13 还保存编译命令和 adapter 校验信息。

然后在有匹配 GPU 的运行机器上检查：

```bash
nvidia-smi -L
GPU_DEVICE=0  # 选择实际要使用的 GPU 索引，也可以填写 GPU UUID。
sudo docker run --rm --gpus "device=$GPU_DEVICE" "$IMAGE" --exec sycl-ls
sudo docker run --rm --gpus "device=$GPU_DEVICE" "$IMAGE" --exec sphinxsim-gpu-check
```

`sycl-ls` 应列出 NVIDIA CUDA 设备，探针应输出 `GPU kernel: PASS`。
探针实际执行 GPU kernel，比仅能运行 `nvidia-smi` 更能验证 SYCL/CUDA 运行链路。
构建成功与 GPU 验证成功是两个独立步骤。

## 7. 运行计算算例

下面示例在构建上下文目录中执行，使用仓库自带的 2D 配置。
输入目录须对容器运行用户可写，入口需要在原 JSON 旁生成临时标准化配置。
如果算例引用 STL 等资源，请一并复制并保持相对路径。

```bash
mkdir -p cases results
cp source/examples/input/test_simulation_2d/config.json cases/dambreak-2d.json

# 先运行短算例，验证 JSON 读取、计算及结果写出。
sudo docker run --rm --gpus "device=$GPU_DEVICE" \
  --user "$(id -u):$(id -g)" --cpus 8 --memory 16g --ulimit core=0 \
  -v "$PWD/cases:/cases" -v "$PWD/results:/results" \
  "$IMAGE" /cases/dambreak-2d.json \
  --output /results/dambreak-2d-smoke --end-time 0.02
```

`--end-time` 修改的是仿真的物理结束时间，不是进程运行超时。
去掉它即可按 JSON 中的完整结束时间计算，并为正式计算指定另一个结果目录：

```bash
sudo docker run --rm --gpus "device=$GPU_DEVICE" \
  --user "$(id -u):$(id -g)" --cpus 8 --memory 16g --ulimit core=0 \
  -v "$PWD/cases:/cases" -v "$PWD/results:/results" \
  "$IMAGE" /cases/dambreak-2d.json --output /results/dambreak-2d-full
```

输出目录必须不存在或为空，已有结果不会被覆盖。输出包含实际配置及 VTP 等结果，
可在其他机器上使用 ParaView 查看。`--memory` 限制的是主机 RAM，不限制 GPU 显存。

长算例可把 `--rm` 替换为 `-d --name sphinxsim-case-01`，然后用
`sudo docker logs -f sphinxsim-case-01` 查看进度；结束后检查容器退出状态并自行清理容器。
容器入口本身不设置算例运行超时。

### 多 GPU

每个算例容器通过 `--gpus device=...` 绑定一张卡，多个容器使用不同结果目录。
生产运行建议用 `nvidia-smi -L` 显示的 GPU UUID 指定设备，避免依赖索引顺序。

当前单个算例只使用一个 SYCL device，不会自动跨卡分解计算或合并显存。
`--gpus all` 仅增加可见设备，不代表单算例多卡加速；同卡其他任务仍会竞争显存和计算资源。

## 8. 将镜像交付到其他机器

不使用镜像仓库时，可将所选镜像导出为压缩包：

```bash
# 在构建机器执行；IMAGE 应设为需要交付的镜像名。
set -o pipefail
sudo docker save "$IMAGE" | gzip -1 > sphinxsim-image.tar.gz
sha256sum sphinxsim-image.tar.gz > sphinxsim-image.tar.gz.sha256
```

将两个文件复制到目标机器，在同一目录执行：

```bash
sha256sum -c sphinxsim-image.tar.gz.sha256
sudo docker load -i sphinxsim-image.tar.gz
```

加载后保留原镜像标签，无需在目标机器重新编译。目标 GPU 架构、驱动及 Container Toolkit
仍需满足要求；随后重新执行第 6 节 GPU 探针和第 7 节短算例验证。

也可以推送到自己有权限的镜像仓库：

```bash
sudo docker tag "$IMAGE" registry.example.com/team/sphinxsim:3090-cuda13
sudo docker login registry.example.com
sudo docker push registry.example.com/team/sphinxsim:3090-cuda13
```

请替换仓库地址、项目和标签，标签应明确反映实际 GPU/工具链组合。

## 9. 常见问题与可复现范围

| 问题 | 检查方法 |
|---|---|
| `COPY` 找不到 `source/`、安装包或入口脚本 | 确认已生成完整上下文，且 `docker build` 最后的路径指向上下文根目录 |
| 准备脚本拒绝目标目录 | 使用仓库外的新目录，脚本不会覆盖现有目录 |
| 缺少安装包或 SHA-256 不匹配 | 按锁定清单重新获取对应文件，执行 `dependencies.py <版本> --check` |
| 编译被杀或出现 OOM | 检查主机及构建容器内存，降低 `BUILD_JOBS` 或增加可用内存 |
| `build.sh` 提示 classic builder 不可用 | 改用已配置的 buildx/BuildKit，参考直接构建方式 |
| Docker 无法分配 GPU | 检查主机驱动、NVIDIA Container Toolkit 及 Docker GPU 配置 |
| SYCL 无法找到 CUDA 设备或探针失败 | 检查 `--gpus`、镜像架构、驱动兼容性及 adapter 加载日志 |
| 输入配置或结果目录权限不足 | 确认挂载目录可被 `--user` 指定的 UID/GID 写入，输入不要只读挂载 |
| 提示结果目录不为空 | 更换输出目录，或自行归档已有结果后重新运行 |

基础镜像使用固定 digest，归档依赖按 SHA-256 校验，Python 直接依赖版本已固定。
APT 软件包和部分间接依赖未做完整快照锁定，因此不同时间重建可能产生不同镜像 ID。
请保留构建日志和镜像内的依赖清单，并在目标硬件上验收自己的算例。

短算例与 GPU 探针用于验证运行环境，不等于物理精度或长期稳定性验收。
调用底层 Python 扩展时，2D 和 3D 模块应放在不同进程中导入，避免已有的 pybind 类型重复注册问题。

## 10. 将构建包分发给其他人

只需打包本目录的源码文件：Dockerfile、运行入口、辅助脚本、依赖锁和许可证。
不要包含本地安装器缓存、下载目录、构建产物或镜像。
不需要附带兄弟目录中的历史报告、基准脚本或原始源码。可将目录重命名为
`sphinxsim-docker-build` 后压缩：

```bash
# 在该目录的上一级执行，压缩包放在目录外。
tar -czf sphinxsim-docker-build.tar.gz sphinxsim-docker-build/
sha256sum sphinxsim-docker-build.tar.gz > sphinxsim-docker-build.tar.gz.sha256
```

接收者解压构建包、下载完整 SPHinXsim 源码，再执行文首的 `build-from-source.sh` 即可。
不要把已经生成的 `source/`、`downloads/`、`logs/` 或镜像导出包放进这个构建工具目录。
公开依赖按锁定版本获取；CUDA 12 的 Codeplay 安装器由接收者自行从供应商取得。
`packages/` 中的获取说明和许可证应随包保留。


## 通用 Agent 接入

本目录构建原生求解基础镜像。继续构建 Agent 与桌面派生镜像、配置 MCP 和安装 Skill，
请见 [通用 Agent MCP 使用指南](../../../integrations/agent/MCP_GUIDE.md)。
这些构建文件不包含调度服务、账号系统或私有镜像仓库依赖。
