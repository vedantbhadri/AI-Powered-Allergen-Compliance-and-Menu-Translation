import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from services.menu_parser import parse_ocr_lines


class MenuParserTests(unittest.TestCase):
    def test_takeout_menu_price_lines_become_individual_dishes(self):
        lines = [
            "culines",
            "GOURMET VEGAN CURSINE",
            "TAKEOUT MENU",
            "BURGERS",
            "Classic American Burger, $10.99",
            "Hamburger, $9.99",
            "Cheesy Burger, $8.50",
            "Hawaiian Burger, $11",
            "HOTDOGS",
            "Classic HotDog. $6",
            "Cheesy HotDog, $8.50",
            "XL HotDog, $10.99",
            "DRINKS",
            "Coke, $2.99",
            "Juice, $3.50",
            "Coffee, $4.50",
            "Order here: www.culines.com",
            "218-237-5684",
        ]

        dishes = parse_ocr_lines(lines)

        self.assertEqual(len(dishes), 10)
        self.assertEqual(dishes[0], {
            "name": "Classic American Burger",
            "description": "",
        })
        self.assertEqual(dishes[-1]["name"], "Coffee")
        self.assertEqual(dishes[4]["name"], "Classic HotDog")
        self.assertFalse(any("TAKEOUT" in dish["name"] for dish in dishes))
        self.assertFalse(any("culines" in dish["name"].lower() for dish in dishes))

    def test_description_lines_attach_to_previous_priced_dish(self):
        dishes = parse_ocr_lines([
            "MAINS",
            "Market Fish $28",
            "Lemon butter, potatoes and seasonal greens",
            "Vegan Curry $24",
            "Coconut, chickpeas and basmati rice",
        ])

        self.assertEqual(dishes, [
            {
                "name": "Market Fish",
                "description": "Lemon butter, potatoes and seasonal greens",
            },
            {
                "name": "Vegan Curry",
                "description": "Coconut, chickpeas and basmati rice",
            },
        ])

    def test_price_free_menu_retains_adjacent_line_fallback(self):
        dishes = parse_ocr_lines(["Soup of the day", "Ask staff for today's soup"])
        self.assertEqual(dishes, [{
            "name": "Soup of the day",
            "description": "Ask staff for today's soup",
        }])

    def test_separate_right_aligned_prices_and_wrapped_descriptions(self):
        lines = [
            "ENTRÉE",
            "A1. Thai Spring Rolls",
            "$8.5",
            "Deep-fried vegetable spring rolls, with sweet chilli sauce.",
            "A3. Money Bags",
            "$9.5",
            "Deep-fried minced pork and vegetable money bags,",
            "served with sweet chilli sauce.",
            "A18. Wontons",
            "$15",
            "House made steamed pork and herb wontons with black",
            "bean chilli sauce.",
            "SOUP",
            "(Mild, Medium, Hot)",
            "T2. Tom Yum Gai",
            "$20",
            "Fresh lemon juice with chicken and smooth chicken stock",
            "combined with galangal, lemongrass, and Thai herbs.",
        ]

        dishes = parse_ocr_lines(lines)

        self.assertEqual(dishes, [
            {
                "name": "Thai Spring Rolls",
                "description": "Deep-fried vegetable spring rolls, with sweet chilli sauce.",
            },
            {
                "name": "Money Bags",
                "description": "Deep-fried minced pork and vegetable money bags, served with sweet chilli sauce.",
            },
            {
                "name": "Wontons",
                "description": "House made steamed pork and herb wontons with black bean chilli sauce.",
            },
            {
                "name": "Tom Yum Gai",
                "description": "Fresh lemon juice with chicken and smooth chicken stock combined with galangal, lemongrass, and Thai herbs.",
            },
        ])

    def test_menu1_extracts_all_fourteen_expected_items(self):
        lines = [
            "ENTRÉE",
            "A1. Thai Spring Rolls", "$8.5", "Spring roll description",
            "A2. Curry Puff", "$12.5", "Curry puff description",
            "A3. Money Bags", "$9.5", "Money bags description",
            "A4. Crispy Prawns", "$13.5", "Crispy prawns description",
            "A5. Pork Spare Ribs", "$14", "Pork ribs description",
            "A6. Chicken Satay", "$13.5", "Chicken satay description",
            "A8. Fish Cakes", "$12", "Fish cakes description",
            "A9. Mixed Entrée", "$14", "Mixed entrée description",
            "A11. Salt & Pepper Squid", "$15", "Squid description",
            "A14. Fresh Salmon Wings", "$18", "Salmon description",
            "A18. Wontons", "$15", "Wontons description",
            "SOUP", "(Mild, Medium, Hot)",
            "T2. Tom Yum Gai", "$20", "Tom Yum Gai description",
            "T4. Tom Yum Goong", "$22", "Tom Yum Goong description",
            "T5. Tom Yum Talay", "$22", "Tom Yum Talay description",
        ]

        dishes = parse_ocr_lines(lines)

        self.assertEqual([dish["name"] for dish in dishes], [
            "Thai Spring Rolls",
            "Curry Puff",
            "Money Bags",
            "Crispy Prawns",
            "Pork Spare Ribs",
            "Chicken Satay",
            "Fish Cakes",
            "Mixed Entrée",
            "Salt & Pepper Squid",
            "Fresh Salmon Wings",
            "Wontons",
            "Tom Yum Gai",
            "Tom Yum Goong",
            "Tom Yum Talay",
        ])


if __name__ == "__main__":
    unittest.main()
