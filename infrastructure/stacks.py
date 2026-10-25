"""Composable CDK constructs for one isolated Token Budget environment."""

from __future__ import annotations

from typing import Any, Final

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import (
    aws_backup as backup,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_rds as rds,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk import (
    aws_ssm as ssm,
)
from constructs import Construct

from .config import EnvironmentConfig

DATABASE_PORT: Final[int] = 5432
WEB_PORT: Final[int] = 8000
SCANNER_PORT: Final[int] = 3310
EVIDENCE_PREFIX: Final[str] = "evidence/"
PERSONAL_DATA_PREFIX: Final[str] = "personal-data/"
QUARANTINE_PREFIX: Final[str] = "quarantine/"


class EncryptionConstruct(Construct):
    """Customer-managed keys with environment-specific aliases."""

    def __init__(self, scope: Construct, construct_id: str, config: EnvironmentConfig) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.database_key = self._key("database", "RDS and database credentials")
        self.storage_key = self._key("storage", "Evidence and Personal_Data object storage")
        self.backup_key = self._key("backup", "Production recovery points")

    def _key(self, name: str, description: str) -> kms.Key:
        key = kms.Key(
            self,
            f"{name.title()}Key",
            alias=f"alias/{self.config.prefix}-{name}",
            description=f"{description} for {self.config.environment}",
            enable_key_rotation=True,
            removal_policy=(
                RemovalPolicy.RETAIN if self.config.is_production else RemovalPolicy.DESTROY
            ),
        )
        key.add_to_resource_policy(
            iam.PolicyStatement(
                sid=f"OwnerAdministration{name.title()}",
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountRootPrincipal()],
                actions=["kms:*"],
                resources=["*"],
            )
        )
        return key


class NetworkConstruct(Construct):
    """Private network boundaries, explicit security groups, and VPC endpoints."""

    def __init__(self, scope: Construct, construct_id: str, config: EnvironmentConfig) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name=f"{config.prefix}-vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name=f"public-{config.environment}",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name=f"application-{config.environment}",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=20,
                ),
                ec2.SubnetConfiguration(
                    name=f"data-{config.environment}",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )
        self.endpoint_security_group = self._security_group("Endpoint", "VPC endpoint")
        self.alb_security_group = self._security_group("Alb", "public edge to application")
        self.web_security_group = self._security_group("Web", "ALB to web tasks")
        self.worker_security_group = self._security_group("Worker", "worker to private services")
        self.scanner_security_group = self._security_group("Scanner", "worker to malware scanner")
        self.database_security_group = self._security_group("Database", "application to RDS")
        self._wire_security_groups()
        self._add_interface_endpoints()

    def _security_group(self, name: str, description: str) -> ec2.SecurityGroup:
        return ec2.SecurityGroup(
            self,
            f"{name}SecurityGroup",
            vpc=self.vpc,
            security_group_name=f"{self.config.prefix}-{name.lower()}-sg",
            description=f"{description} ({self.config.environment})",
            allow_all_outbound=False,
        )

    def _wire_security_groups(self) -> None:
        self.alb_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "TLS from the public edge"
        )
        self.alb_security_group.add_egress_rule(
            self.web_security_group, ec2.Port.tcp(WEB_PORT), "Only web task traffic"
        )
        self.web_security_group.add_ingress_rule(
            self.alb_security_group, ec2.Port.tcp(WEB_PORT), "Only the ALB may reach web tasks"
        )
        self.worker_security_group.add_egress_rule(
            self.database_security_group, ec2.Port.tcp(DATABASE_PORT), "Worker PostgreSQL access"
        )
        self.web_security_group.add_egress_rule(
            self.database_security_group, ec2.Port.tcp(DATABASE_PORT), "Web PostgreSQL access"
        )
        self.database_security_group.add_ingress_rule(
            self.web_security_group, ec2.Port.tcp(DATABASE_PORT), "Web to private RDS"
        )
        self.database_security_group.add_ingress_rule(
            self.worker_security_group, ec2.Port.tcp(DATABASE_PORT), "Worker to private RDS"
        )
        self.scanner_security_group.add_ingress_rule(
            self.worker_security_group, ec2.Port.tcp(SCANNER_PORT), "Only worker scan requests"
        )
        for source_group in (
            self.web_security_group,
            self.worker_security_group,
            self.scanner_security_group,
        ):
            source_group.add_egress_rule(
                self.endpoint_security_group,
                ec2.Port.tcp(443),
                "Private AWS service endpoint access",
            )
        self.worker_security_group.add_egress_rule(
            self.scanner_security_group, ec2.Port.tcp(SCANNER_PORT), "Worker scan requests"
        )

    def _add_interface_endpoints(self) -> None:
        services = {
            "SecretsManager": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            "SystemManager": ec2.InterfaceVpcEndpointAwsService.SSM,
            "Kms": ec2.InterfaceVpcEndpointAwsService.KMS,
            "CloudWatchLogs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
        }
        for name, service in services.items():
            self.vpc.add_interface_endpoint(
                f"{name}Endpoint",
                service=service,
                subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                security_groups=[self.endpoint_security_group],
                private_dns_enabled=True,
            )
            self.endpoint_security_group.add_ingress_rule(
                self.web_security_group, ec2.Port.tcp(443), "Web task endpoint access"
            )
            self.endpoint_security_group.add_ingress_rule(
                self.worker_security_group, ec2.Port.tcp(443), "Worker endpoint access"
            )
            self.endpoint_security_group.add_ingress_rule(
                self.scanner_security_group, ec2.Port.tcp(443), "Scanner endpoint access"
            )

    def add_storage_endpoint(self, bucket: s3.IBucket) -> ec2.GatewayVpcEndpoint:
        """Add a bucket-scoped S3 gateway endpoint after storage exists."""

        endpoint = self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)],
        )
        endpoint.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:AbortMultipartUpload",
                    "s3:ListBucket",
                ],
                resources=[bucket.bucket_arn, bucket.arn_for_objects("*")],
                conditions={
                    "StringEquals": {"aws:PrincipalAccount": self.config.account or Aws.ACCOUNT_ID}
                },
            )
        )
        return endpoint


class DataConstruct(Construct):
    """Private, encrypted, backed-up PostgreSQL and runtime configuration values."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        network: NetworkConstruct,
        encryption: EncryptionConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.database_secret = secretsmanager.Secret(
            self,
            "DatabaseSecret",
            secret_name=f"{config.prefix}/database",
            description=f"Private PostgreSQL credentials for {config.environment}",
            encryption_key=encryption.database_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"tokenbudget"}',
                generate_string_key="password",
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.RETAIN if config.is_production else RemovalPolicy.DESTROY,
        )
        self.database = rds.DatabaseInstance(
            self,
            "Database",
            instance_identifier=f"{config.prefix}-postgres",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_4,
            ),
            credentials=rds.Credentials.from_secret(self.database_secret),
            vpc=network.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[network.database_security_group],
            publicly_accessible=False,
            storage_encrypted=True,
            storage_encryption_key=encryption.database_key,
            multi_az=config.is_production,
            allocated_storage=100,
            max_allocated_storage=500,
            backup_retention=Duration.days(35 if config.is_production else 14),
            preferred_backup_window="03:00-03:30",
            preferred_maintenance_window="sun:04:00-sun:04:30",
            deletion_protection=config.is_production,
            delete_automated_backups=not config.is_production,
            removal_policy=RemovalPolicy.RETAIN if config.is_production else RemovalPolicy.DESTROY,
        )
        self.runtime_parameter = ssm.StringParameter(
            self,
            "RuntimeParameter",
            parameter_name=f"/{config.prefix}/runtime/config",
            description=f"Non-source runtime configuration for {config.environment}",
            string_value=config.runtime_parameter_value,
            tier=ssm.ParameterTier.STANDARD,
            simple_name=False,
        )


class StorageConstruct(Construct):
    """One private versioned bucket with separated sensitive prefixes."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        network: NetworkConstruct,
        encryption: EncryptionConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.bucket = s3.Bucket(
            self,
            "EvidenceBucket",
            bucket_name=f"{config.prefix}-private-data",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=encryption.storage_key,
            bucket_key_enabled=True,
            enforce_ssl=True,
            versioned=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireIncompleteUploads",
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                ),
                s3.LifecycleRule(
                    id="RetainEvidenceVersions",
                    noncurrent_version_expiration=Duration.days(
                        400 if config.is_production else 90
                    ),
                ),
            ],
            removal_policy=RemovalPolicy.RETAIN if config.is_production else RemovalPolicy.DESTROY,
            auto_delete_objects=not config.is_production,
        )
        self.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyInsecureTransport",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:*"],
                resources=[self.bucket.bucket_arn, self.bucket.arn_for_objects("*")],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )
        self.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyPublicObjectAcl",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:PutObjectAcl"],
                resources=[self.bucket.arn_for_objects("*")],
                conditions={"StringEquals": {"s3:x-amz-acl": ["public-read", "public-read-write"]}},
            )
        )
        network.add_storage_endpoint(self.bucket)


class AccessConstruct(Construct):
    """Separate ECS task roles with prefix- and action-scoped data access."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        network: NetworkConstruct,
        encryption: EncryptionConstruct,
        data: DataConstruct,
        storage: StorageConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.web_role = self._role("Web", "web task")
        self.worker_role = self._role("Worker", "worker task")
        self.scanner_role = self._role("Scanner", "scanner task")
        self._grant_common_runtime(self.web_role, data)
        self._grant_common_runtime(self.worker_role, data)
        self._grant_database(self.web_role, data, encryption)
        self._grant_database(self.worker_role, data, encryption)
        self._grant_storage(self.web_role, storage, encryption, write_prefix=QUARANTINE_PREFIX)
        self._grant_storage(self.worker_role, storage, encryption, write_prefix=EVIDENCE_PREFIX)
        self._grant_storage(self.worker_role, storage, encryption, write_prefix=QUARANTINE_PREFIX)
        self._grant_storage(
            self.worker_role, storage, encryption, write_prefix=PERSONAL_DATA_PREFIX
        )
        self._grant_storage(
            self.scanner_role,
            storage,
            encryption,
            write_prefix=QUARANTINE_PREFIX,
            read_only=True,
        )
        self._grant_scanner(self.worker_role, network)

    def _role(self, name: str, description: str) -> iam.Role:
        return iam.Role(
            self,
            f"{name}TaskRole",
            role_name=f"{self.config.prefix}-{name.lower()}-task",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=f"Least-privilege {description} role for {self.config.environment}",
            max_session_duration=Duration.hours(1),
        )

    @staticmethod
    def _policy(role: iam.Role, actions: list[str], resources: list[str], sid: str) -> None:
        role.add_to_policy(
            iam.PolicyStatement(
                sid=sid,
                effect=iam.Effect.ALLOW,
                actions=actions,
                resources=resources,
            )
        )

    def _grant_common_runtime(self, role: iam.Role, data: DataConstruct) -> None:
        self._policy(
            role,
            ["secretsmanager:GetSecretValue"],
            [data.database_secret.secret_arn],
            "ReadDatabaseSecret",
        )
        self._policy(
            role,
            ["ssm:GetParameter"],
            [data.runtime_parameter.parameter_arn],
            "ReadRuntimeParameter",
        )

    def _grant_database(
        self, role: iam.Role, data: DataConstruct, encryption: EncryptionConstruct
    ) -> None:
        self._policy(
            role,
            ["kms:Decrypt", "kms:DescribeKey"],
            [encryption.database_key.key_arn],
            "DecryptDatabaseSecret",
        )

    def _grant_storage(
        self,
        role: iam.Role,
        storage: StorageConstruct,
        encryption: EncryptionConstruct,
        *,
        write_prefix: str,
        read_only: bool = False,
    ) -> None:
        actions = ["s3:GetObject", "s3:GetObjectVersion"]
        if not read_only:
            actions.extend(["s3:PutObject", "s3:AbortMultipartUpload"])
        self._policy(
            role,
            actions,
            [storage.bucket.arn_for_objects(f"{write_prefix}*")],
            f"Storage{write_prefix.title().replace('-', '')}",
        )
        self._policy(
            role,
            ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"],
            [encryption.storage_key.key_arn],
            f"StorageKey{write_prefix.title().replace('-', '')}",
        )

    def _grant_scanner(self, role: iam.Role, network: NetworkConstruct) -> None:
        self._policy(
            role,
            ["logs:CreateLogStream", "logs:PutLogEvents"],
            [
                (
                    f"arn:aws:logs:{self.config.region}:"
                    f"{self.config.account or Aws.ACCOUNT_ID}:"
                    f"log-group:/aws/ecs/{self.config.prefix}:*"
                )
            ],
            "WriteSanitizedLogs",
        )


class BackupConstruct(Construct):
    """AWS Backup vault and daily RDS recovery points with production retention."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: EnvironmentConfig,
        encryption: EncryptionConstruct,
        data: DataConstruct,
    ) -> None:
        super().__init__(scope, construct_id)
        self.vault = backup.BackupVault(
            self,
            "BackupVault",
            backup_vault_name=f"{config.prefix}-vault",
            encryption_key=encryption.backup_key,
            removal_policy=RemovalPolicy.RETAIN if config.is_production else RemovalPolicy.DESTROY,
        )
        self.plan = backup.BackupPlan(
            self,
            "BackupPlan",
            backup_plan_name=f"{config.prefix}-daily",
        )
        self.plan.add_rule(
            backup.BackupPlanRule(
                backup_vault=self.vault,
                rule_name=f"{config.environment}-daily-rds",
                schedule_expression=events.Schedule.cron(minute="0", hour="2"),
                start_window=Duration.hours(1),
                completion_window=Duration.hours(3),
                delete_after=Duration.days(400 if config.is_production else 35),
            )
        )
        self.plan.add_selection(
            "RdsSelection",
            resources=[backup.BackupResource.from_arn(data.database.instance_arn)],
        )


class TokenBudgetPlatformStack(Stack):
    """Root stack: validates config before creating any child construct."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: EnvironmentConfig,
        **kwargs: Any,
    ) -> None:
        config.validate()
        stack_kwargs: dict[str, Any] = dict(kwargs)
        stack_kwargs.setdefault("env", {"account": config.account, "region": config.region})
        super().__init__(scope, construct_id, **stack_kwargs)
        self.config = config
        Tags.of(self).add("Environment", config.environment)
        Tags.of(self).add("Application", "token-budget-platform")
        self.encryption = EncryptionConstruct(self, "Encryption", config)
        self.network = NetworkConstruct(self, "Network", config)
        self.data = DataConstruct(self, "Data", config, self.network, self.encryption)
        self.storage = StorageConstruct(self, "Storage", config, self.network, self.encryption)
        self.access = AccessConstruct(
            self,
            "Access",
            config,
            self.network,
            self.encryption,
            self.data,
            self.storage,
        )
        self.backup = BackupConstruct(self, "Backup", config, self.encryption, self.data)
        CfnOutput(self, "Environment", value=config.environment)
        CfnOutput(self, "EvidenceBucketName", value=self.storage.bucket.bucket_name)
        CfnOutput(self, "DatabaseSecretArn", value=self.data.database_secret.secret_arn)
