from aiogram.fsm.state import State, StatesGroup


class AdminImportState(StatesGroup):
    waiting_for_document = State()
    waiting_for_confirmation = State()

