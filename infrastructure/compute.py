"""ECS Fargate constructs: one shared image runs web, worker, and scanner tasks."""

from __future__ import annotations

from typing import Final

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_logs as logs,
)
from constructs import Construct

from .config import EnvironmentConfig
from .stacks import (
    WEB_PORT,
    AccessConstruct,
    DataConstruct,
    EncryptionConstruct,
    NetworkConstruct,
    StorageConstruct,
)

SCANNER_PORT: Final[int] = 3310
WORKER_POLL_SECONDS: Final[int] = 10
HEALTH_CHECK_PATH: Final[str] = "/health/ready"
WEB_CPU: Final[int] = 512
WEB_MEMORY: Final[int] = 1024
WORKER_CPU: Final[int] = 512
WORKER_MEMORY: Final[int] = 1024
SCANNER_CPU: Final[int] = 1024
SCANNER_MEMORY: Final[int] = 2048


class ComputeConstruct(Construct):
    """Private multi-AZ ECS Fargate tasks for web, worker, and scanner."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        network: NetworkConstruct,
        encryption: EncryptionConstruct,
        data: DataConstruct,
        storage: StorageConstruct,
        access: AccessConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config

        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"{config.prefix}-cluster",
            vpc=network.vpc,
            container_insights=True,
        )

        self.repository = ecr.Repository(
            self,
            "AppRepository",
            repository_name=f"{config.prefix}-app",
            removal_policy=RemovalPolicy.RETAIN if config.is_production else RemovalPolicy.DESTROY,
            empty_on_delete=not config.is_production,
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(max_image_count=25, description="Retain 25 most recent images")
            ],
        )

        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/aws/ecs/{config.prefix}",
            retention=logs.RetentionDays.ONE_MONTH
            if config.is_production
            else logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.RETAIN if config.is_production else RemovalPolicy.DESTROY,
        )

        shared_env = self._shared_environment(config, data, storage, encryption)

        self.web_task = self._fargate_task(
            "Web",
            cpu=WEB_CPU,
            memory=WEB_MEMORY,
            role=access.web_role,
            command=[
                "gunicorn",
                "config.wsgi:application",
                "--bind",
                f"0.0.0.0:{WEB_PORT}",
                "--workers",
                "2",
                "--timeout",
                "30",
            ],
            port=WEB_PORT,
            environment=shared_env,
            health_path=HEALTH_CHECK_PATH,
            security_group=network.web_security_group,
        )

        self.worker_task = self._fargate_task(
            "Worker",
            cpu=WORKER_CPU,
            memory=WORKER_MEMORY,
            role=access.worker_role,
            command=["python", "manage.py", "worker", "--poll-interval", str(WORKER_POLL_SECONDS)],
            environment=shared_env,
            security_group=network.worker_security_group,
        )

        self.scanner_task = self._fargate_task(
            "Scanner",
            cpu=SCANNER_CPU,
            memory=SCANNER_MEMORY,
            role=access.scanner_role,
            command=["clamd", "--foreground"],
            port=SCANNER_PORT,
            environment={
                "ENVIRONMENT": config.environment,
                "SERVICE_ROLE": "scanner",
            },
            security_group=network.scanner_security_group,
        )

        CfnOutput(self, "ClusterArn", value=self.cluster.cluster_arn)
        CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)

    def _shared_environment(
        self,
        config: EnvironmentConfig,
        data: DataConstruct,
        storage: StorageConstruct,
        encryption: EncryptionConstruct,
    ) -> dict[str, str]:
        """Environment variables for the shared application image."""
        return {
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "ENVIRONMENT": config.environment,
            "AWS_S3_BUCKET": storage.bucket.bucket_name,
            "AWS_KMS_KEY_ID": encryption.storage_key.key_arn,
            "DATABASE_SECRET_ARN": data.database_secret.secret_arn,
            "RUNTIME_PARAMETER_NAME": data.runtime_parameter.parameter_name,
            "CLOUDWATCH_NAMESPACE": f"TokenBudgetPlatform/{config.environment}",
            "AWS_XRAY_ENABLED": "true",
            "SECURE_COOKIES": "true",
        }

    def _fargate_task(
        self,
        name: str,
        *,
        cpu: int,
        memory: int,
        role: iam.IRole,
        command: list[str],
        environment: dict[str, str],
        security_group: ec2.ISecurityGroup,
        port: int | None = None,
        health_path: str | None = None,
    ) -> ecs.FargateTaskDefinition:
        task_def = ecs.FargateTaskDefinition(
            self,
            f"{name}Task",
            family=f"{self.config.prefix}-{name.lower()}",
            cpu=cpu,
            memory_limit_mib=memory,
            task_role=role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        container = task_def.add_container(
            f"{name}Container",
            image=ecs.ContainerImage.from_ecr_repository(self.repository, tag="latest"),
            command=command,
            environment={**environment, "SERVICE_ROLE": name.lower()},
            logging=ecs.LogDrivers.aws_logs(
                log_group=self.log_group,
                stream_prefix=name.lower(),
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", f"curl -f http://localhost:{port}{health_path} || exit 1"]
                if port and health_path
                else ["CMD-SHELL", "exit 0"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        if port:
            container.add_port_mappings(
                ecs.PortMapping(container_port=port, protocol=ecs.Protocol.TCP)
            )

        return task_def
