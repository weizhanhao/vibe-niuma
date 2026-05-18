"""aliyun_provisioner —— 用阿里云 OpenAPI 自动开 ECS + 配安全组 + EIP。

Plan 11 · M2.T11.

设计：
- AliyunEcsClient Protocol 定义我们要用的 ~5 个方法 → 测试塞 fake client。
- AliyunProvisioner 类编排流程：create_instance → wait_running →
  allocate_public_ip → authorize_security_group → 返 ProvisionResult。
- 失败任何阶段抛 ProvisionError；调用方决定要不要回滚（DeleteInstance）。
- 真实 SDK (alibabacloud-ecs20140526) 走 default factory，**lazy import**：
  base install 不强制装 SDK，业务员要用阿里云自动部署才 `pip install vibe-niuma[aliyun]`。

业务员要做的事（不可代劳，写在 wizard 教程里）：
- 注册阿里云 + 实名 + 充值
- 在 RAM 控制台创 access key + 给 AliyunECSFullAccess 权限
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# ── 数据契约 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AliyunCredentials:
    access_key_id: str
    access_key_secret: str
    region_id: str = "cn-hangzhou"

    def __post_init__(self) -> None:
        # 不能为空 —— 早 raise 比阿里云 401 错得更清楚
        if not self.access_key_id:
            raise ValueError("access_key_id 不能为空")
        if not self.access_key_secret:
            raise ValueError("access_key_secret 不能为空")
        if not self.region_id:
            raise ValueError("region_id 不能为空")


@dataclass(frozen=True)
class EcsSpec:
    """业务员选 / 默认推荐的实例规格。"""
    instance_type: str = "ecs.t6-c1m4.large"   # 4C 8G，跑 vibe-niuma 够用
    image_id: str = "aliyun_3_x64_20G_alibase_20221102.vhd"  # Alibaba Cloud Linux 3
    system_disk_size_gb: int = 40
    internet_max_bandwidth_mbps: int = 5
    password: str = ""   # 跑 bootstrap 用；wizard 自动生成强密码

    @staticmethod
    def with_random_password() -> "EcsSpec":
        import secrets
        # 阿里云密码规则：8-30 字符，含大写 + 小写 + 数字 + 特殊（避开 ' " ~ % \）
        chars = "Aa1!" + secrets.token_urlsafe(20).replace("-", "x").replace("_", "y")[:16]
        return EcsSpec(password=chars[:24])


@dataclass
class ProvisionResult:
    instance_id: str
    public_ip: str
    region_id: str
    password: str   # 业务员 ssh 上去跑 bootstrap 用，跑完销毁
    open_ports: list[int] = field(default_factory=list)


# ── 异常 ─────────────────────────────────────────────────────────────


class ProvisionError(Exception):
    """所有 provisioner 异常的基类。"""


class AliyunAuthError(ProvisionError):
    """access key 无效 / 权限不够（典型业务员给的 key 没有 AliyunECSFullAccess）。"""


class AliyunQuotaError(ProvisionError):
    """配额不够（业务员账号没充值 / 此地域 instance type 没库存）。"""


class AliyunSDKNotInstalled(ProvisionError):
    """alibabacloud-ecs20140526 SDK 没装。`pip install vibe-niuma[aliyun]`。"""


# ── Protocol：测试 fake + 真实 SDK 都实现这个 ─────────────────────


class AliyunEcsClient(Protocol):
    """provisioner 真正调的几个方法。真实 SDK wrap 一层适配。"""

    def create_instance(self, spec: EcsSpec, region_id: str) -> str:
        """创实例，返 instance_id。"""
        ...

    def describe_instance_status(self, instance_id: str) -> str:
        """返 'Pending' / 'Starting' / 'Running' / 'Stopped' / ..."""
        ...

    def allocate_public_ip(self, instance_id: str) -> str:
        """给实例分配公网 IP，返 IP 字符串。"""
        ...

    def get_security_group_id(self, instance_id: str) -> str:
        """实例所属的 default 安全组 ID。"""
        ...

    def authorize_security_group(
        self, security_group_id: str, ports: list[int], cidr: str = "0.0.0.0/0",
    ) -> None:
        """放行入站端口。"""
        ...

    def delete_instance(self, instance_id: str) -> None:
        """删实例（幂等：已删的不抛错）。"""
        ...


# ── Orchestration ────────────────────────────────────────────────────


class AliyunProvisioner:
    """业务员视角：粘 access key + 选地域 + 点开始 → 5-10 分钟后拿到 public_ip + ssh 密码。

    使用：
        provisioner = AliyunProvisioner(creds)
        try:
            result = provisioner.provision(EcsSpec.with_random_password())
            print(result.public_ip)
        except ProvisionError as e:
            print(f"开机失败：{e}")
    """

    # vibe-niuma 必须放行的端口
    DEFAULT_PORTS = [22, 9000, *range(5100, 5200)]
    POLL_INTERVAL_SECONDS = 5.0
    DEFAULT_RUNNING_TIMEOUT = 300.0  # 5 分钟够阿里云开机

    def __init__(
        self,
        creds: AliyunCredentials,
        *,
        client_factory: Optional[Callable[[AliyunCredentials], AliyunEcsClient]] = None,
    ) -> None:
        self.creds = creds
        self._client_factory = client_factory or _default_client_factory
        self._client: Optional[AliyunEcsClient] = None

    def _get_client(self) -> AliyunEcsClient:
        if self._client is None:
            self._client = self._client_factory(self.creds)
        return self._client

    def provision(
        self,
        spec: EcsSpec,
        *,
        running_timeout: float = DEFAULT_RUNNING_TIMEOUT,
        ports: Optional[list[int]] = None,
    ) -> ProvisionResult:
        """开机 → 等 Running → 分配公网 IP → 配安全组 → 返结果。"""
        client = self._get_client()
        if not spec.password:
            spec = EcsSpec.with_random_password()
        open_ports = ports if ports is not None else self.DEFAULT_PORTS

        logger.info("provision: create_instance type=%s region=%s",
                    spec.instance_type, self.creds.region_id)
        instance_id = client.create_instance(spec, self.creds.region_id)

        try:
            logger.info("provision: wait running instance_id=%s timeout=%ss",
                        instance_id, running_timeout)
            self._wait_running(client, instance_id, running_timeout)

            logger.info("provision: allocate public ip")
            public_ip = client.allocate_public_ip(instance_id)

            logger.info("provision: authorize security group ports=%s", open_ports)
            sg_id = client.get_security_group_id(instance_id)
            client.authorize_security_group(sg_id, open_ports)
        except Exception:
            # 调用方决定要不要 rollback（DeleteInstance）—— provisioner 不自动删
            # 因为业务员可能想看错误日志后手动续命
            logger.exception("provision 失败，instance_id=%s 已创建但未完工", instance_id)
            raise

        return ProvisionResult(
            instance_id=instance_id,
            public_ip=public_ip,
            region_id=self.creds.region_id,
            password=spec.password,
            open_ports=list(open_ports),
        )

    def rollback(self, instance_id: str) -> None:
        """provision 半路失败时调，清掉 instance（业务员不被计费）。幂等。"""
        try:
            self._get_client().delete_instance(instance_id)
            logger.info("rollback: deleted instance %s", instance_id)
        except Exception as exc:
            # 已不存在 / 删的过程出错都不抛 —— rollback 本身的失败不该再炸
            logger.warning("rollback: delete_instance %s 失败：%s", instance_id, exc)

    def _wait_running(
        self,
        client: AliyunEcsClient,
        instance_id: str,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_status = "unknown"
        while time.monotonic() < deadline:
            status = client.describe_instance_status(instance_id)
            if status != last_status:
                logger.info("instance %s status=%s", instance_id, status)
                last_status = status
            if status == "Running":
                return
            if status in {"Stopped", "Failed", "Deleted"}:
                raise ProvisionError(
                    f"instance {instance_id} 进入异常状态 {status}（不再 Running 路径上）"
                )
            time.sleep(self.POLL_INTERVAL_SECONDS)
        raise ProvisionError(
            f"等 instance {instance_id} Running 超过 {timeout}s（最后状态 {last_status}）。"
            f"建议去阿里云控制台看为什么开机这么慢"
        )


# ── 真实 SDK 适配层（lazy import） ────────────────────────────────


def _default_client_factory(creds: AliyunCredentials) -> AliyunEcsClient:
    """生产模式：装真 SDK 客户端。SDK 没装时给清晰错误。"""
    try:
        return _RealAliyunEcsClient(creds)
    except ImportError as e:
        raise AliyunSDKNotInstalled(
            "未装 alibabacloud-ecs20140526 SDK。\n"
            "运行：pip install 'alibabacloud-ecs20140526>=4.0' "
            "'alibabacloud-tea-openapi>=0.3' 'alibabacloud-tea-util>=0.3'"
        ) from e


class _RealAliyunEcsClient:
    """真实 SDK 的薄包装。所有调用映射到 ecs20140526 client。"""

    def __init__(self, creds: AliyunCredentials) -> None:
        # 这里 import 是 lazy：测试不会触发，没装 SDK 才报错
        from alibabacloud_ecs20140526.client import Client as EcsClient  # type: ignore[import-not-found]
        from alibabacloud_tea_openapi import models as oapi_models  # type: ignore[import-not-found]

        config = oapi_models.Config(
            access_key_id=creds.access_key_id,
            access_key_secret=creds.access_key_secret,
            region_id=creds.region_id,
        )
        config.endpoint = f"ecs.{creds.region_id}.aliyuncs.com"
        self._client = EcsClient(config)
        self._region = creds.region_id

    def create_instance(self, spec: EcsSpec, region_id: str) -> str:
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore[import-not-found]

        req = ecs_models.CreateInstanceRequest(
            region_id=region_id,
            instance_type=spec.instance_type,
            image_id=spec.image_id,
            password=spec.password,
            system_disk=ecs_models.CreateInstanceRequestSystemDisk(
                size=str(spec.system_disk_size_gb),
                category="cloud_essd",
            ),
            internet_max_bandwidth_out=spec.internet_max_bandwidth_mbps,
            internet_charge_type="PayByTraffic",
            instance_charge_type="PostPaid",
            instance_name="vibe-niuma-auto",
            description="vibe-niuma 自动开机（业务员零运维）",
        )
        resp = self._client.create_instance(req)
        return resp.body.instance_id

    def describe_instance_status(self, instance_id: str) -> str:
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore[import-not-found]

        req = ecs_models.DescribeInstanceStatusRequest(
            region_id=self._region,
            instance_id=[instance_id],
        )
        resp = self._client.describe_instance_status(req)
        items = resp.body.instance_statuses.instance_status if resp.body.instance_statuses else []
        for it in items:
            if it.instance_id == instance_id:
                return it.status
        return "unknown"

    def allocate_public_ip(self, instance_id: str) -> str:
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore[import-not-found]

        req = ecs_models.AllocatePublicIpAddressRequest(instance_id=instance_id)
        resp = self._client.allocate_public_ip_address(req)
        return resp.body.ip_address

    def get_security_group_id(self, instance_id: str) -> str:
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore[import-not-found]

        req = ecs_models.DescribeInstancesRequest(
            region_id=self._region,
            instance_ids=f'["{instance_id}"]',
        )
        resp = self._client.describe_instances(req)
        instances = resp.body.instances.instance if resp.body.instances else []
        if not instances:
            raise ProvisionError(f"实例 {instance_id} describe 返空 —— 已被删？")
        sg_ids = instances[0].security_group_ids.security_group_id
        if not sg_ids:
            raise ProvisionError(f"实例 {instance_id} 没有 security group（罕见）")
        return sg_ids[0]

    def authorize_security_group(
        self, security_group_id: str, ports: list[int], cidr: str = "0.0.0.0/0",
    ) -> None:
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore[import-not-found]

        # 把连续端口段压缩，减少 API 调用次数
        for start, end in _compress_port_ranges(ports):
            req = ecs_models.AuthorizeSecurityGroupRequest(
                region_id=self._region,
                security_group_id=security_group_id,
                ip_protocol="tcp",
                port_range=f"{start}/{end}",
                source_cidr_ip=cidr,
                description="vibe-niuma auto-provisioned",
            )
            try:
                self._client.authorize_security_group(req)
            except Exception as exc:
                # 端口规则已存在 → 阿里云会返特定错误码，可忽略
                if "AlreadyExists" in str(exc) or "InvalidParameter.Conflict" in str(exc):
                    continue
                raise

    def delete_instance(self, instance_id: str) -> None:
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore[import-not-found]

        req = ecs_models.DeleteInstanceRequest(
            instance_id=instance_id,
            force=True,
        )
        try:
            self._client.delete_instance(req)
        except Exception as exc:
            if "InvalidInstanceId.NotFound" in str(exc):
                return  # 幂等
            raise


def _compress_port_ranges(ports: list[int]) -> list[tuple[int, int]]:
    """[22, 9000, 5100, 5101, 5102, ..., 5199] → [(22,22), (9000,9000), (5100,5199)]。"""
    if not ports:
        return []
    sorted_ports = sorted(set(ports))
    ranges: list[tuple[int, int]] = []
    start = sorted_ports[0]
    prev = start
    for p in sorted_ports[1:]:
        if p == prev + 1:
            prev = p
            continue
        ranges.append((start, prev))
        start = prev = p
    ranges.append((start, prev))
    return ranges
