import enum


class UserRole(str, enum.Enum):
    USER = "user"
    AGENCY_ADMIN = "agency_admin"
    PLATFORM_ADMIN = "platform_admin"


class VerificationTier(str, enum.Enum):
    BASIC = "BASIC"
    VERIFIED = "VERIFIED"
    TRUSTED = "TRUSTED"


class PlanStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PlanType(str, enum.Enum):
    STANDARD = "STANDARD"
    CORPORATE = "CORPORATE"


class PackageStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class OfferStatus(str, enum.Enum):
    PENDING = "PENDING"
    COUNTERED = "COUNTERED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class MemberStatus(str, enum.Enum):
    INTERESTED = "INTERESTED"
    APPROVED = "APPROVED"
    COMMITTED = "COMMITTED"
    LEFT = "LEFT"
    REMOVED = "REMOVED"


class MemberRole(str, enum.Enum):
    CREATOR = "CREATOR"
    MEMBER = "MEMBER"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


class EscrowStatus(str, enum.Enum):
    HELD = "HELD"
    PARTIAL_RELEASE = "PARTIAL_RELEASE"
    RELEASED = "RELEASED"
    REFUNDED = "REFUNDED"


class TransferStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    MANUAL = "MANUAL"


class AgencyMemberRole(str, enum.Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    AGENT = "AGENT"


class AgencyVerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class MessageType(str, enum.Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    SYSTEM = "SYSTEM"
    POLL = "POLL"
    OFFER = "OFFER"


class NotificationType(str, enum.Enum):
    PLAN_OFFER = "PLAN_OFFER"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_REJECTED = "OFFER_REJECTED"
    OFFER_COUNTERED = "OFFER_COUNTERED"
    PAYMENT_DUE = "PAYMENT_DUE"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    TRIP_COMPLETED = "TRIP_COMPLETED"
    GROUP_INVITE = "GROUP_INVITE"
    NEW_FOLLOWER = "NEW_FOLLOWER"
    PROFILE_VIEW = "PROFILE_VIEW"
    REVIEW_RECEIVED = "REVIEW_RECEIVED"
    REFERRAL_JOINED = "REFERRAL_JOINED"
    LOYALTY_EARNED = "LOYALTY_EARNED"
    SYSTEM = "SYSTEM"


class KycDocumentType(str, enum.Enum):
    AADHAAR = "AADHAAR"
    PAN = "PAN"
    GST_CERTIFICATE = "GST_CERTIFICATE"
    BANK_STATEMENT = "BANK_STATEMENT"
    OTHER = "OTHER"


class KycStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class LoyaltyAction(str, enum.Enum):
    TRIP_COMPLETION = "TRIP_COMPLETION"
    REFERRAL_BONUS = "REFERRAL_BONUS"
    WELCOME_BONUS = "WELCOME_BONUS"
    ADMIN_ADJUST = "ADMIN_ADJUST"
    REDEMPTION = "REDEMPTION"
    EXPIRY = "EXPIRY"


class DisputeStatus(str, enum.Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class WalletTransactionType(str, enum.Enum):
    REFERRAL_EARN = "REFERRAL_EARN"
    WITHDRAWAL = "WITHDRAWAL"
    ADJUSTMENT = "ADJUSTMENT"
    PAYMENT_DEDUCTION = "PAYMENT_DEDUCTION"
