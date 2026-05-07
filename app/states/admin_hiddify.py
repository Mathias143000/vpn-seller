from aiogram.fsm.state import State, StatesGroup


class AdminHiddifyState(StatesGroup):
    waiting_for_import_document = State()
    waiting_for_import_confirmation = State()
    waiting_for_name = State()
    waiting_for_country = State()
    waiting_for_base_url = State()
    waiting_for_admin_proxy_path = State()
    waiting_for_client_proxy_path = State()
    waiting_for_api_key = State()
