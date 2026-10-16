"""The hand-tuned demo shop: Rahman Store, a neighbourhood grocery in Mohakhali, Dhaka.

Every name, number and amount here is invented for the demo.
"""

from __future__ import annotations

from data.specs import CustomerSpec, ExpenseSpec, OneOffSpec, ShopSpec, SupplierSpec

HERO_SHOP_ID = "rahman-store"

SUPPLIERS = [
    # Rice, lentils and flour, delivered every Saturday on 7-day credit, paid by bKash.
    SupplierSpec("Rahim Traders", "01712345678", "bkash", "Rice, lentils & flour", 0.45, 7, 5, 7,
                 aliases=("Rahim", "রহিম ট্রেডার্স", "রহিম")),
    # Soap, biscuits and snacks every other Tuesday, 14-day terms, paid by bank transfer.
    SupplierSpec("Meghna Distributors", "2051334007812", "bank", "Toiletries & snacks", 0.25, 14, 1, 14,
                 aliases=("Meghna", "মেঘনা ডিস্ট্রিবিউটরস", "মেঘনা")),
    # Edible oil and sugar every Wednesday, paid on delivery by bKash.
    SupplierSpec("Bismillah Oil Agency", "01819876543", "bkash", "Oil & sugar", 0.18, 7, 2, 0,
                 aliases=("Bismillah", "Bismillah Oil", "বিসমিল্লাহ অয়েল")),
    # Milk, eggs and bread every morning, paid in cash.
    SupplierSpec("Fresh Dairy Point", "01556677889", "cash", "Dairy, eggs & bread", 0.12, 1, 0, 0),
]

CUSTOMERS = [
    # An office canteen nearby that buys on credit and settles roughly monthly by bKash.
    CustomerSpec("Skyline Garments canteen", "01730900900", 24, 0.995, 3.0),
    CustomerSpec("Jamal Uddin", "01711223344", 6, 0.99, 1.4),
    CustomerSpec("Sumi Akter", "01822334455", 9, 0.98, 1.2),
    CustomerSpec("Babul Mia", "01933445566", 24, 0.86, 1.0),
    CustomerSpec("Rina Begum", "01644556677", 7, 0.99, 1.1),
    CustomerSpec("Kamal Hossain", "01755667788", 12, 0.97, 1.0),
    CustomerSpec("Shahana Parvin", "01866778899", 8, 0.99, 0.9),
    CustomerSpec("Abdul Karim", "01977889900", 15, 0.95, 0.9),
    CustomerSpec("Nasima Khatun", "01688990011", 6, 0.99, 0.8),
    CustomerSpec("Rafiq Islam", "01799001122", 10, 0.97, 0.8),
    CustomerSpec("Tania Sultana", "01300112233", 7, 0.99, 0.7),
    CustomerSpec("Habib Rahman", "01411223344", 18, 0.92, 0.7),
    CustomerSpec("Moushumi Das", "01522334455", 5, 0.99, 0.7),
    CustomerSpec("Selim Reza", "01633445566", 11, 0.97, 0.6),
    CustomerSpec("Farida Yasmin", "01744556677", 8, 0.98, 0.6),
    CustomerSpec("Monir Hossain", "01855667788", 20, 0.90, 0.6),
    CustomerSpec("Lipi Akter", "01966778899", 6, 0.99, 0.5),
    CustomerSpec("Jahangir Alam", "01577889900", 13, 0.96, 0.5),
    CustomerSpec("Shirin Akter", "01388990011", 7, 0.99, 0.5),
    CustomerSpec("Delwar Hossain", "01499001122", 16, 0.94, 0.4),
    CustomerSpec("Rokeya Begum", "01600112244", 9, 0.98, 0.4),
]

EXPENSES = [
    ExpenseSpec("Shop rent", "Haji Abdul Malek (landlord)", "Rent", 22_000, 15, "bank"),
    ExpenseSpec("Electricity bill", "DESCO prepaid recharge", "Utilities", 3_800, 18, "bkash"),
    ExpenseSpec("Helper's salary", "Rubel (shop helper)", "Wages", 9_000, 1, "cash"),
]


def hero_spec(history_days: int) -> ShopSpec:
    return ShopSpec(
        shop_id=HERO_SHOP_ID,
        name="Rahman Store",
        owner_name="Mizanur Rahman",
        shop_type="grocery",
        area="Mohakhali",
        district="Dhaka",
        division="Dhaka",
        urbanicity="urban",
        history_days=history_days,
        base_sales=13_500,
        growth=0.08,
        volatility=0.16,
        margin=0.15,
        mobile_share=0.42,
        nagad_share=0.25,
        baki_share=0.13,
        collect_rate=0.97,
        supplier_on_time=0.93,
        buffer_days=4.0,
        drawings_rate=0.45,
        misc_expense_rate=0.012,
        discipline=0.78,
        shock=None,
        opening={"cash": 8_000, "bkash": 12_000, "nagad": 3_000, "bank": 25_000},
        suppliers=SUPPLIERS,
        customers=CUSTOMERS,
        expenses=EXPENSES,
        owner_phone="01711000111",
        bkash_number="01711000222",
        nagad_number="01811000222",
        bank_account="1701500012345",
        wallet_float=25_000,
        one_offs=[
            OneOffSpec("2026-10-08", "owner_drawing", 28_000, "Owner withdrawal",
                       "Sister's wedding: gift and costs", "cash"),
        ],
        tags={"hero": "true"},
    )
