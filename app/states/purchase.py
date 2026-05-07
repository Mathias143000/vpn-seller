from aiogram.fsm.state import State, StatesGroup


class PurchaseState(StatesGroup):
    selecting_fulfillment = State()
    selecting_server = State()
    entering_promo = State()
    confirming_plan = State()
