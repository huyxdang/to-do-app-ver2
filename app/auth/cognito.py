import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode

import requests

from app.config import settings

logger = logging.getLogger(__name__)

# Cognito JWKS URL để xác minh token
COGNITO_JWKS_URL = (
    f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/"
    f"{settings.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
)

# Cache JWKS keys để tránh gọi API liên tục
_jwks_cache: dict[str, Any] | None = None


def _get_cognito_client() -> Any:
    """Tạo Cognito IDP client với credentials từ settings."""
    return boto3.client(
        "cognito-idp",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def _get_jwks() -> dict[str, Any]:
    """Lấy JWKS từ Cognito để xác minh JWT token, có cache."""
    global _jwks_cache
    if _jwks_cache is None:
        try:
            response = requests.get(COGNITO_JWKS_URL, timeout=10)
            response.raise_for_status()
            _jwks_cache = response.json()
        except Exception as exc:
            logger.error("Failed to fetch JWKS from Cognito: %s", exc)
            raise RuntimeError(f"Cannot fetch JWKS: {exc}") from exc
    return _jwks_cache


class CognitoError(Exception):
    """Base exception cho Cognito operations."""

    def __init__(self, message: str, code: str = "CognitoError") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class UserAlreadyExistsError(CognitoError):
    """Người dùng đã tồn tại trong Cognito User Pool."""

    def __init__(self, email: str) -> None:
        super().__init__(
            message=f"User with email '{email}' already exists.",
            code="UsernameExistsException",
        )


class UserNotFoundError(CognitoError):
    """Người dùng không tồn tại trong Cognito User Pool."""

    def __init__(self, email: str) -> None:
        super().__init__(
            message=f"User with email '{email}' not found.",
            code="UserNotFoundException",
        )


class InvalidConfirmationCodeError(CognitoError):
    """Mã xác minh không hợp lệ hoặc đã hết hạn."""

    def __init__(self) -> None:
        super().__init__(
            message="The confirmation code is invalid or has expired.",
            code="CodeMismatchException",
        )


class InvalidCredentialsError(CognitoError):
    """Thông tin đăng nhập không đúng."""

    def __init__(self) -> None:
        super().__init__(
            message="Incorrect username or password.",
            code="NotAuthorizedException",
        )


class TokenVerificationError(CognitoError):
    """Token không hợp lệ hoặc đã hết hạn."""

    def __init__(self, reason: str = "Token verification failed.") -> None:
        super().__init__(message=reason, code="TokenVerificationError")


def sign_up(email: str, password: str, display_name: str) -> dict[str, Any]:
    """
    Đăng ký tài khoản mới trong Cognito User Pool.
    Gửi verification email tới địa chỉ email đã đăng ký.

    Returns:
        dict chứa UserSub (UUID của user trong Cognito) và trạng thái xác minh.
    """
    client = _get_cognito_client()
    try:
        response = client.sign_up(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=email,
            Password=password,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "name", "Value": display_name},
            ],
        )
        logger.info("User signed up successfully: %s", email)
        return {
            "user_sub": response["UserSub"],
            "user_confirmed": response["UserConfirmed"],
            "destination": response.get("CodeDeliveryDetails", {}).get("Destination", email),
        }
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Sign-up failed for %s: %s - %s", email, error_code, error_message)

        if error_code == "UsernameExistsException":
            raise UserAlreadyExistsError(email) from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def confirm_sign_up(email: str, confirmation_code: str) -> bool:
    """
    Xác minh tài khoản bằng mã OTP được gửi qua email.
    Kích hoạt tài khoản sau khi xác minh thành công.

    Returns:
        True nếu xác minh thành công.
    """
    client = _get_cognito_client()
    try:
        client.confirm_sign_up(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=email,
            ConfirmationCode=confirmation_code,
        )
        logger.info("User confirmed successfully: %s", email)
        return True
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Confirmation failed for %s: %s - %s", email, error_code, error_message)

        if error_code in ("CodeMismatchException", "ExpiredCodeException"):
            raise InvalidConfirmationCodeError() from exc
        if error_code == "UserNotFoundException":
            raise UserNotFoundError(email) from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def resend_confirmation_code(email: str) -> dict[str, Any]:
    """
    Gửi lại mã xác minh tới email đã đăng ký.
    Sử dụng khi mã cũ đã hết hạn hoặc người dùng không nhận được.

    Returns:
        dict chứa thông tin kênh gửi mã (email/SMS).
    """
    client = _get_cognito_client()
    try:
        response = client.resend_confirmation_code(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=email,
        )
        logger.info("Confirmation code resent to: %s", email)
        delivery_details = response.get("CodeDeliveryDetails", {})
        return {
            "destination": delivery_details.get("Destination", email),
            "delivery_medium": delivery_details.get("DeliveryMedium", "EMAIL"),
        }
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Resend code failed for %s: %s - %s", email, error_code, error_message)

        if error_code == "UserNotFoundException":
            raise UserNotFoundError(email) from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def initiate_auth(email: str, password: str) -> dict[str, Any]:
    """
    Xác thực người dùng với Cognito và trả về các JWT tokens.

    Returns:
        dict chứa AccessToken, IdToken, RefreshToken và thời gian hết hạn.
    """
    client = _get_cognito_client()
    try:
        response = client.initiate_auth(
            ClientId=settings.COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": email,
                "PASSWORD": password,
            },
        )
        auth_result = response.get("AuthenticationResult", {})
        logger.info("User authenticated successfully: %s", email)
        return {
            "access_token": auth_result.get("AccessToken"),
            "id_token": auth_result.get("IdToken"),
            "refresh_token": auth_result.get("RefreshToken"),
            "expires_in": auth_result.get("ExpiresIn", 3600),
            "token_type": auth_result.get("TokenType", "Bearer"),
        }
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Auth failed for %s: %s - %s", email, error_code, error_message)

        if error_code == "NotAuthorizedException":
            raise InvalidCredentialsError() from exc
        if error_code == "UserNotFoundException":
            raise UserNotFoundError(email) from exc
        if error_code == "UserNotConfirmedException":
            raise CognitoError(
                message="User account is not confirmed. Please verify your email.",
                code="UserNotConfirmedException",
            ) from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def forgot_password(email: str) -> dict[str, Any]:
    """
    Khởi tạo quy trình reset mật khẩu.
    Cognito gửi mã OTP tới email đã đăng ký.
    Trả về thông tin trung lập để bảo mật (không tiết lộ email có tồn tại hay không).

    Returns:
        dict chứa thông tin kênh gửi mã reset.
    """
    client = _get_cognito_client()
    try:
        response = client.forgot_password(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=email,
        )
        delivery_details = response.get("CodeDeliveryDetails", {})
        logger.info("Password reset initiated for: %s", email)
        return {
            "destination": delivery_details.get("Destination", ""),
            "delivery_medium": delivery_details.get("DeliveryMedium", "EMAIL"),
        }
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Forgot password failed for %s: %s - %s", email, error_code, error_message)

        # Không throw UserNotFoundError để tránh lộ thông tin user tồn tại
        if error_code == "UserNotFoundException":
            logger.info("Forgot password requested for non-existent user: %s (suppressed)", email)
            return {"destination": "", "delivery_medium": "EMAIL"}
        raise CognitoError(message=error_message, code=error_code) from exc


def confirm_forgot_password(email: str, confirmation_code: str, new_password: str) -> bool:
    """
    Xác nhận reset mật khẩu với mã OTP và mật khẩu mới.
    Toàn bộ session cũ bị hủy sau khi đổi mật khẩu thành công.

    Returns:
        True nếu reset mật khẩu thành công.
    """
    client = _get_cognito_client()
    try:
        client.confirm_forgot_password(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=email,
            ConfirmationCode=confirmation_code,
            Password=new_password,
        )
        logger.info("Password reset confirmed for: %s", email)
        return True
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning(
            "Confirm forgot password failed for %s: %s - %s", email, error_code, error_message
        )

        if error_code in ("CodeMismatchException", "ExpiredCodeException"):
            raise InvalidConfirmationCodeError() from exc
        if error_code == "UserNotFoundException":
            raise UserNotFoundError(email) from exc
        if error_code == "InvalidPasswordException":
            raise CognitoError(
                message="Password does not meet the required policy.",
                code="InvalidPasswordException",
            ) from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def verify_token(token: str) -> dict[str, Any]:
    """
    Xác minh JWT access token hoặc ID token từ Cognito.
    Kiểm tra chữ ký, thời hạn, và issuer của token.

    Returns:
        dict chứa decoded claims của token (sub, email, exp, iat, ...).

    Raises:
        TokenVerificationError nếu token không hợp lệ.
    """
    try:
        # Lấy header của token để tìm key ID (kid)
        headers = jwt.get_unverified_headers(token)
        kid = headers.get("kid")
        if not kid:
            raise TokenVerificationError("Token header missing 'kid'.")

        # Lấy JWKS và tìm public key tương ứng
        jwks = _get_jwks()
        public_key = None
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                public_key = key_data
                break

        if public_key is None:
            # Xóa cache và thử lại một lần nếu key không tìm thấy
            global _jwks_cache
            _jwks_cache = None
            jwks = _get_jwks()
            for key_data in jwks.get("keys", []):
                if key_data.get("kid") == kid:
                    public_key = key_data
                    break

        if public_key is None:
            raise TokenVerificationError("Public key not found for the given token.")

        # Xây dựng RSA public key từ JWKS
        rsa_key = jwk.construct(public_key)

        # Xác minh chữ ký của token
        message, encoded_signature = token.rsplit(".", 1)
        decoded_signature = base64url_decode(encoded_signature.encode("utf-8"))
        if not rsa_key.verify(message.encode("utf-8"), decoded_signature):
            raise TokenVerificationError("Token signature verification failed.")

        # Giải mã và kiểm tra claims
        expected_issuer = (
            f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/"
            f"{settings.COGNITO_USER_POOL_ID}"
        )
        claims = jwt.decode(
            token,
            rsa_key.public_key(),
            algorithms=["RS256"],
            options={"verify_at_hash": False},
        )

        # Kiểm tra issuer hợp lệ
        if claims.get("iss") != expected_issuer:
            raise TokenVerificationError(
                f"Invalid token issuer. Expected '{expected_issuer}', got '{claims.get('iss')}'."
            )

        # Kiểm tra token_use (access hoặc id token)
        token_use = claims.get("token_use")
        if token_use not in ("access", "id"):
            raise TokenVerificationError(
                f"Invalid token_use '{token_use}'. Must be 'access' or 'id'."
            )

        logger.debug("Token verified successfully for sub: %s", claims.get("sub"))
        return claims

    except TokenVerificationError:
        raise
    except JWTError as exc:
        logger.warning("JWT error during token verification: %s", exc)
        raise TokenVerificationError(f"JWT error: {exc}") from exc
    except Exception as exc:
        logger.error("Unexpected error during token verification: %s", exc)
        raise TokenVerificationError(f"Token verification failed: {exc}") from exc


def get_user_info(access_token: str) -> dict[str, Any]:
    """
    Lấy thông tin user từ Cognito bằng access token.
    Trả về các user attributes như email, name, sub.

    Returns:
        dict chứa username và user attributes (email, name, sub, email_verified, ...).
    """
    client = _get_cognito_client()
    try:
        response = client.get_user(AccessToken=access_token)
        # Chuyển đổi danh sách attributes thành dict để dễ sử dụng
        attributes: dict[str, str] = {
            attr["Name"]: attr["Value"]
            for attr in response.get("UserAttributes", [])
        }
        return {
            "username": response.get("Username"),
            "sub": attributes.get("sub"),
            "email": attributes.get("email"),
            "email_verified": attributes.get("email_verified", "false").lower() == "true",
            "name": attributes.get("name", ""),
            "attributes": attributes,
        }
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Get user info failed: %s - %s", error_code, error_message)

        if error_code == "NotAuthorizedException":
            raise TokenVerificationError("Access token is invalid or expired.") from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    """
    Làm mới access token và ID token bằng refresh token.

    Returns:
        dict chứa AccessToken mới, IdToken mới và thời gian hết hạn.
    """
    client = _get_cognito_client()
    try:
        response = client.initiate_auth(
            ClientId=settings.COGNITO_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )
        auth_result = response.get("AuthenticationResult", {})
        logger.info("Tokens refreshed successfully.")
        return {
            "access_token": auth_result.get("AccessToken"),
            "id_token": auth_result.get("IdToken"),
            "expires_in": auth_result.get("ExpiresIn", 3600),
            "token_type": auth_result.get("TokenType", "Bearer"),
        }
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning("Token refresh failed: %s - %s", error_code, error_message)

        if error_code == "NotAuthorizedException":
            raise TokenVerificationError("Refresh token is invalid or has been revoked.") from exc
        raise CognitoError(message=error_message, code=error_code) from exc


def admin_update_user_attributes(
    cognito_sub: str, attributes: dict[str, str]
) -> bool:
    """
    Cập nhật user attributes trong Cognito bằng admin API.
    Sử dụng để đồng bộ profile changes (tên, avatar) sang Cognito.

    Args:
        cognito_sub: Cognito username (thường là email hoặc sub UUID).
        attributes: dict các attribute cần cập nhật (key-value).

    Returns:
        True nếu cập nhật thành công.
    """
    client = _get_cognito_client()
    user_attributes = [
        {"Name": name, "Value": value} for name, value in attributes.items()
    ]
    try:
        client.admin_update_user_attributes(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=cognito_sub,
            UserAttributes=user_attributes,
        )
        logger.info("Admin updated attributes for user: %s", cognito_sub)
        return True
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_message = exc.response["Error"]["Message"]
        logger.warning(
            "Admin update attributes failed for %s: %s - %s",
            cognito_sub,
            error_code,
            error_message,
        )

        if error_code == "UserNotFoundException":
            raise UserNotFoundError(cognito_sub) from exc
        raise CognitoError(message=error_message, code=error_code) from exc