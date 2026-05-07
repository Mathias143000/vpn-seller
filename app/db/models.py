from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserRole(str, enum.Enum):
    USER = "user"
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    SUPPORT = "support"


class UserChannel(str, enum.Enum):
    TELEGRAM = "telegram"
    VK = "vk"
    WHATSAPP = "whatsapp"


class PlanProvisioningMode(str, enum.Enum):
    INVENTORY = "inventory"
    HIDDIFY = "hiddify"
    MTPROXY = "mtproxy"
    AUTO = "auto"


class OrderFulfillmentMode(str, enum.Enum):
    AUTO = "auto"
    INVENTORY = "inventory"
    MTPROXY = "mtproxy"
    HIDDIFY_SERVER = "hiddify_server"
    HIDDIFY_SUPERKEY = "hiddify_superkey"


class KeyStatus(str, enum.Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ISSUED = "issued"
    DISABLED = "disabled"
    BROKEN = "broken"
    ARCHIVED = "archived"


class OrderStatus(str, enum.Enum):
    CREATED = "created"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    ISSUED = "issued"
    PAID_BUT_NOT_ISSUED = "paid_but_not_issued"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    PAYMENT_FAILED = "payment_failed"
    EXPIRED_RESERVATION = "expired_reservation"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    FAILED = "failed"
    REFUNDED = "refunded"


class ImportBatchStatus(str, enum.Enum):
    PREVIEW = "preview"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryJobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    DELIVERED = "delivered"
    FAILED = "failed"


class ProcessingStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class PromoDiscountType(str, enum.Enum):
    PERCENT = "percent"
    FIXED = "fixed"


class ServerHealthStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(unique=True, nullable=False, index=True)
    vk_user_id: Mapped[int | None] = mapped_column(unique=True, nullable=True, index=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True)
    delivery_channel: Mapped[str] = mapped_column(String(16), default=UserChannel.TELEGRAM.value, nullable=False)
    active_promo_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default=UserRole.USER.value, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(default=False, nullable=False)


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_days: Mapped[int] = mapped_column(nullable=False)
    price_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    low_stock_threshold: Mapped[int] = mapped_column(default=5, nullable=False)
    provisioning_mode: Mapped[str] = mapped_column(
        String(32),
        default=PlanProvisioningMode.AUTO.value,
        nullable=False,
    )


class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_total: Mapped[int] = mapped_column(default=0, nullable=False)
    rows_imported: Mapped[int] = mapped_column(default=0, nullable=False)
    rows_rejected: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=ImportBatchStatus.PREVIEW.value, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class VPNKey(TimestampMixin, Base):
    __tablename__ = "vpn_keys"
    __table_args__ = (
        Index("ix_vpn_keys_plan_id_status", "plan_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    key_value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=KeyStatus.AVAILABLE.value, nullable=False)
    reserved_by_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), unique=True, nullable=True)
    issued_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    imported_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HiddifyServer(TimestampMixin, Base):
    __tablename__ = "hiddify_servers"
    __table_args__ = (
        UniqueConstraint("base_url", "admin_proxy_path", name="uq_hiddify_servers_base_admin_path"),
        Index("ix_hiddify_servers_is_active_last_used_at", "is_active", "last_used_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_name: Mapped[str] = mapped_column(String(128), default="Без страны", nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    admin_proxy_path: Mapped[str] = mapped_column(String(128), nullable=False)
    client_proxy_path: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    panel_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_health_status: Mapped[str] = mapped_column(
        String(32),
        default=ServerHealthStatus.UNKNOWN.value,
        nullable=False,
    )
    last_healthcheck_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HiddifyServerUsageSnapshot(Base):
    __tablename__ = "hiddify_server_usage_snapshots"
    __table_args__ = (
        Index("ix_hiddify_usage_snapshots_server_sampled_at", "server_id", "sampled_at"),
        Index("ix_hiddify_usage_snapshots_sampled_at", "sampled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("hiddify_servers.id"), nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    total_users_count: Mapped[int | None] = mapped_column(nullable=True)
    active_users_count: Mapped[int | None] = mapped_column(nullable=True)
    active_users_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_current_usage_gb: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    average_user_usage_gb: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    usage_sample_users_count: Mapped[int] = mapped_column(default=0, nullable=False)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ShopSetting(TimestampMixin, Base):
    __tablename__ = "shop_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_id_created_at", "user_id", "created_at"),
        Index("ix_orders_provider_payment_id", "provider_payment_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default=OrderStatus.CREATED.value, nullable=False)
    amount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    amount_currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    original_amount_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    discount_amount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    promo_code_id: Mapped[int | None] = mapped_column(ForeignKey("promo_codes.id"), nullable=True)
    promo_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fulfillment_mode: Mapped[str] = mapped_column(
        String(64),
        default=OrderFulfillmentMode.AUTO.value,
        nullable=False,
    )
    preferred_hiddify_server_id: Mapped[int | None] = mapped_column(ForeignKey("hiddify_servers.id"), nullable=True)
    reserved_key_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_keys.id"), unique=True, nullable=True)
    issued_key_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_keys.id"), unique=True, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_payment_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=PaymentStatus.PENDING.value, nullable=False)
    amount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    amount_currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    provider_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    raw_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_events_provider_event_id"),
        Index("ix_payment_events_provider_payment_id_received_at", "provider_payment_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(32), default=ProcessingStatus.RECEIVED.value, nullable=False)


class PromoCode(TimestampMixin, Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_type: Mapped[str] = mapped_column(String(16), nullable=False)
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_uses: Mapped[int | None] = mapped_column(nullable=True)
    used_count: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"
    __table_args__ = (
        UniqueConstraint("promo_code_id", "user_id", name="uq_promo_redemptions_code_user"),
        UniqueConstraint("order_id", name="uq_promo_redemptions_order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    promo_code_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    discount_amount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeliveryJob(TimestampMixin, Base):
    __tablename__ = "delivery_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_delivery_jobs_dedupe_key"),
        Index("ix_delivery_jobs_status_next_retry_at", "status", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=DeliveryJobStatus.PENDING.value, nullable=False)
    attempts_count: Mapped[int] = mapped_column(default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
