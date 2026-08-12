from __future__ import annotations

import base64
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin import build_admin_menu, build_import_confirmation, build_import_waiting_actions
from app.services.exceptions import AccessDeniedError
from app.services.users import UsersService
from app.states.admin_import import AdminImportState

router = Router()


@router.message(Command("admin_import"))
async def admin_import_command(message: Message, state: FSMContext, app_user, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await message.answer("Для этого нужны права оператора 🙂")
        return
    await state.set_state(AdminImportState.waiting_for_document)
    await message.answer(
        (
            "<b>Импорт ключей 📥</b>\n\n"
            "Пришлите <code>.xlsx</code> с листом <code>keys</code> или typed SQLite от Golden VPN.\n"
            "Сначала покажу preview, а импорт начнем только после подтверждения."
        ),
        reply_markup=build_import_waiting_actions(),
    )


@router.callback_query(F.data == "admin:import:back")
async def admin_import_back_callback(callback: CallbackQuery, state: FSMContext, app_user, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await callback.answer("Для этого нужны права оператора 🙂", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "<b>Админка 🛠</b>\n\nВыберите нужное действие ниже.",
        reply_markup=build_admin_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:import")
async def admin_import_callback(callback: CallbackQuery, state: FSMContext, app_user, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await callback.answer("Для этого нужны права оператора 🙂", show_alert=True)
        return
    await state.set_state(AdminImportState.waiting_for_document)
    await callback.message.edit_text(
        (
            "<b>Импорт ключей 📥</b>\n\n"
            "Пришлите <code>.xlsx</code> с листом <code>keys</code> или typed SQLite от Golden VPN.\n"
            "Сначала покажу preview, а импорт начнем только после подтверждения."
        ),
        reply_markup=build_import_waiting_actions(),
    )
    await callback.answer()


@router.message(AdminImportState.waiting_for_document, F.document)
async def import_preview_document(message: Message, state: FSMContext, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await message.answer("Для этого нужны права оператора 🙂")
        return
    filename = message.document.file_name
    lower_filename = filename.lower()
    if lower_filename.endswith(".xlsx"):
        import_kind = "xlsx"
        import_service = services.xlsx_import
    elif lower_filename.endswith((".sqlite", ".sqlite3", ".db")):
        import_kind = "sqlite"
        import_service = services.sqlite_import
    else:
        await message.answer("Нужен файл <code>.xlsx</code>, <code>.sqlite</code> или <code>.db</code> 🙂")
        return

    buffer = BytesIO()
    await message.bot.download(message.document, destination=buffer)
    content = buffer.getvalue()
    preview = await import_service.preview(filename=filename, content=content)
    await state.update_data(
        import_filename=filename,
        import_content=base64.b64encode(content).decode("ascii"),
        import_kind=import_kind,
    )
    await state.set_state(AdminImportState.waiting_for_confirmation)
    await message.answer(
        (
            f"<b>Preview для {preview['filename']}</b>\n\n"
            f"Всего строк: <b>{preview['rows_total']}</b>\n"
            f"Валидных: <b>{preview['rows_valid']}</b>\n"
            f"Отклонено: <b>{preview['rows_rejected']}</b>\n"
            f"Типы: <code>{preview.get('types', {})}</code>\n"
            f"Статусы: <code>{preview.get('statuses', {})}</code>\n"
            f"Первые ошибки: <code>{preview['errors'][:5]}</code>"
        ),
        reply_markup=build_import_confirmation(),
    )


@router.callback_query(AdminImportState.waiting_for_confirmation, F.data == "admin:import:confirm")
async def confirm_import_callback(callback: CallbackQuery, state: FSMContext, app_user, services, **_: dict) -> None:
    try:
        UsersService.require_operator(app_user)
    except AccessDeniedError:
        await callback.answer("Для этого нужны права оператора 🙂", show_alert=True)
        return
    data = await state.get_data()
    content = base64.b64decode(data["import_content"])
    import_service = services.sqlite_import if data.get("import_kind") == "sqlite" else services.xlsx_import
    result = await import_service.import_file(
        filename=data["import_filename"],
        content=content,
        uploaded_by_user_id=app_user.id,
    )
    await state.clear()
    await callback.message.edit_text(
        (
            "<b>Импорт завершен ✅</b>\n\n"
            f"Batch #{result['batch_id']}\n"
            f"Импортировано: <b>{result['rows_imported']}</b>\n"
            f"Отклонено: <b>{result['rows_rejected']}</b>"
        ),
        reply_markup=build_import_waiting_actions(),
    )
    await callback.answer("Импорт выполнен ✨")


@router.callback_query(AdminImportState.waiting_for_confirmation, F.data == "admin:import:cancel")
async def cancel_import_callback(callback: CallbackQuery, state: FSMContext, **_: dict) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Импорт отменен 🙂",
        reply_markup=build_import_waiting_actions(),
    )
    await callback.answer()
