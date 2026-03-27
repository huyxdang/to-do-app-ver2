from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.auth.cognito import (
    cognito_sign_up,
    cognito_confirm_sign_up,
    cognito_resend_confirmation_code,
    cognito_initiate_auth,
    cognito_forgot_password,
    cognito_confirm_forgot_password,
)
from app.models.user import User, UserProfile
from app.schemas.user import (
    UserRegisterRequest,
    UserVerifyEmailRequest,
    UserResendVerificationRequest,
    UserLoginRequest,
    UserLoginResponse,
    UserForgotPasswordRequest,
    UserResetPasswordRequest,
    MessageResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký tài khoản mới bằng email và mật khẩu",
)
async def register(
    payload: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Tạo tài khoản mới:
    1. Gọi Cognito sign-up để tạo user và gửi verification email
    2. Lưu thông tin user vào database local với trạng thái chưa xác minh
    """
    # Kiểm tra email đã tồn tại trong DB local chưa
    result = await db.execute(select(User).where(User.email == payload.email))
    existing_user = result.scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email đã được sử dụng. Vui lòng dùng email khác hoặc đăng nhập.",
        )

    # Gọi AWS Cognito để tạo user và gửi email xác minh
    cognito_result = await cognito_sign_up(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )

    cognito_sub: str = cognito_result["UserSub"]

    # Lưu user vào database local
    new_user = User(
        email=payload.email,
        cognito_sub=cognito_sub,
        is_verified=False,
        is_active=True,
    )
    db.add(new_user)
    await db.flush()  # Lấy ID trước khi commit

    # Tạo UserProfile với display_name
    profile = UserProfile(
        user_id=new_user.id,
        display_name=payload.display_name,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(new_user)

    return MessageResponse(
        message="Đăng ký thành công! Vui lòng kiểm tra email để xác minh tài khoản. Link xác minh có hiệu lực trong 24 giờ."
    )


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Xác minh email bằng mã OTP nhận được qua email",
)
async def verify_email(
    payload: UserVerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Xác minh tài khoản:
    1. Gọi Cognito confirm sign-up với mã OTP
    2. Cập nhật trạng thái is_verified trong DB local
    """
    # Kiểm tra user tồn tại trong DB local
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy tài khoản với email này.",
        )

    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tài khoản đã được xác minh. Vui lòng đăng nhập.",
        )

    # Xác nhận OTP với Cognito
    await cognito_confirm_sign_up(email=payload.email, confirmation_code=payload.code)

    # Cập nhật trạng thái xác minh trong DB local
    user.is_verified = True
    await db.commit()

    return MessageResponse(
        message="Xác minh email thành công! Tài khoản của bạn đã được kích hoạt. Vui lòng đăng nhập."
    )


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Gửi lại email xác minh nếu link/mã OTP đã hết hạn",
)
async def resend_verification(
    payload: UserResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Gửi lại mã xác minh:
    - Chỉ gửi lại nếu tài khoản chưa được xác minh
    - Gọi Cognito resend confirmation code
    """
    # Kiểm tra user tồn tại
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    # Trả về thông báo trung lập để bảo mật (không tiết lộ email có tồn tại hay không)
    if user is None:
        return MessageResponse(
            message="Nếu email tồn tại trong hệ thống và chưa được xác minh, bạn sẽ nhận được email xác minh mới trong vài phút."
        )

    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tài khoản đã được xác minh. Vui lòng đăng nhập.",
        )

    # Gọi Cognito gửi lại mã xác minh
    await cognito_resend_confirmation_code(email=payload.email)

    return MessageResponse(
        message="Nếu email tồn tại trong hệ thống và chưa được xác minh, bạn sẽ nhận được email xác minh mới trong vài phút."
    )


@router.post(
    "/login",
    response_model=UserLoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Đăng nhập bằng email và mật khẩu, nhận JWT token",
)
async def login(
    payload: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> UserLoginResponse:
    """
    Đăng nhập:
    1. Gọi Cognito initiate auth để xác thực credentials
    2. Kiểm tra trạng thái tài khoản trong DB local
    3. Trả về access token, refresh token và thông tin user
    """
    # Kiểm tra user tồn tại và đã xác minh
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không chính xác.",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản chưa được xác minh. Vui lòng kiểm tra email và xác minh tài khoản.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ hỗ trợ.",
        )

    # Xác thực credentials với Cognito và lấy tokens
    token_data = await cognito_initiate_auth(
        email=payload.email,
        password=payload.password,
    )

    auth_result = token_data.get("AuthenticationResult", {})
    access_token: str = auth_result.get("AccessToken", "")
    refresh_token: str = auth_result.get("RefreshToken", "")
    id_token: str = auth_result.get("IdToken", "")
    expires_in: int = auth_result.get("ExpiresIn", 3600)

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi xác thực. Vui lòng thử lại sau.",
        )

    # Lấy thông tin profile của user
    profile_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()
    display_name: str = profile.display_name if profile else ""

    return UserLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        token_type="Bearer",
        expires_in=expires_in,
        user_id=str(user.id),
        email=user.email,
        display_name=display_name,
    )


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Yêu cầu reset mật khẩu, gửi link/mã OTP qua email",
)
async def forgot_password(
    payload: UserForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Yêu cầu reset mật khẩu:
    - Trả về thông báo trung lập để bảo mật (không tiết lộ email có tồn tại hay không)
    - Gọi Cognito forgot password nếu user tồn tại và đã xác minh
    - Link reset password có hiệu lực trong 1 giờ
    """
    # Kiểm tra user tồn tại và đã xác minh trước khi gọi Cognito
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    # Trả về thông báo trung lập để tránh email enumeration attack
    neutral_message = MessageResponse(
        message="Nếu email tồn tại trong hệ thống, bạn sẽ nhận được hướng dẫn reset mật khẩu trong vài phút. Link có hiệu lực trong 1 giờ."
    )

    if user is None or not user.is_verified or not user.is_active:
        return neutral_message

    # Gọi Cognito để gửi email reset mật khẩu
    await cognito_forgot_password(email=payload.email)

    return neutral_message


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Đặt lại mật khẩu mới bằng mã OTP từ email",
)
async def reset_password(
    payload: UserResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Đặt lại mật khẩu:
    1. Gọi Cognito confirm forgot password với mã OTP và mật khẩu mới
    2. Cognito tự động hủy toàn bộ session cũ sau khi đổi mật khẩu thành công
    """
    # Kiểm tra user tồn tại
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác nhận không hợp lệ hoặc đã hết hạn. Vui lòng yêu cầu reset mật khẩu lại.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ hỗ trợ.",
        )

    # Gọi Cognito xác nhận reset mật khẩu — toàn bộ session cũ bị hủy tự động
    await cognito_confirm_forgot_password(
        email=payload.email,
        confirmation_code=payload.code,
        new_password=payload.new_password,
    )

    return MessageResponse(
        message="Đặt lại mật khẩu thành công! Toàn bộ phiên đăng nhập cũ đã bị hủy. Vui lòng đăng nhập bằng mật khẩu mới."
    )