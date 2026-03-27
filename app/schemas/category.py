from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class CategoryBase(BaseModel):
    """ベースカテゴリスキーマ"""
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="カテゴリ名 (1-100文字)"
    )
    color: str = Field(
        default="#808080",
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="カテゴリの色 (HEXフォーマット: #RRGGBB)"
    )


class CategoryCreate(CategoryBase):
    """Tạo danh mục mới"""
    pass


class CategoryUpdate(BaseModel):
    """Cập nhật danh mục"""
    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Tên danh mục mới (tùy chọn)"
    )
    color: Optional[str] = Field(
        default=None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Màu danh mục mới dạng HEX (tùy chọn)"
    )

    def has_updates(self) -> bool:
        """Kiểm tra xem có bất kỳ trường nào được cập nhật hay không"""
        return self.name is not None or self.color is not None


class CategoryResponse(CategoryBase):
    """Phản hồi danh mục với thông tin kèm theo"""
    id: int = Field(..., description="ID danh mục")
    user_id: str = Field(..., description="ID người dùng sở hữu danh mục")
    task_count: int = Field(
        default=0,
        description="Số lượng task trong danh mục"
    )
    created_at: datetime = Field(..., description="Thời gian tạo")
    updated_at: datetime = Field(..., description="Thời gian cập nhật cuối")
    is_default: bool = Field(
        default=False,
        description="Có phải danh mục mặc định (Uncategorized) không"
    )

    model_config = ConfigDict(from_attributes=True)


class CategoryListResponse(BaseModel):
    """Danh sách danh mục với số liệu thống kê"""
    categories: list[CategoryResponse] = Field(
        default_factory=list,
        description="Danh sách danh mục"
    )
    total: int = Field(
        default=0,
        description="Tổng số danh mục"
    )


class CategoryDeleteRequest(BaseModel):
    """Yêu cầu xóa danh mục với tùy chọn tái chỉ định"""
    reassign_category_id: Optional[int] = Field(
        default=None,
        description="ID danh mục để tái chỉ định các task (nếu không cung cấp sẽ chuyển sang Uncategorized)"
    )


class CategoryDeleteResponse(BaseModel):
    """Phản hồi khi xóa danh mục"""
    success: bool = Field(
        ...,
        description="Xóa thành công hay không"
    )
    message: str = Field(
        ...,
        description="Thông báo kết quả"
    )
    reassigned_task_count: int = Field(
        default=0,
        description="Số lượng task đã được tái chỉ định"
    )

    model_config = ConfigDict(from_attributes=True)