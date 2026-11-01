from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.workbook.defined_name import DefinedName


BASE_DIR = Path(__file__).resolve().parent.parent
LOGO_PATH = BASE_DIR / "static" / "logo.png"

HEADERS = [
    "Item",
    "Category",
    "Sub-category",
    "Expiry date",
    "Qty",
    "Unit size (g)",
]

CATEGORIES = {
    "Food and Drink": [
        "Water", "Soup", "Beans", "Tinned Tomatoes", "Tinned Fruit",
        "Staples", "Protein", "Other Food",
    ],
    "Fuel": [
        "Wood Logs", "Camping Stove Fuel", "Firelighting", "Other Fuel",
    ],
    "Equipment": [
        "Lighting", "Batteries", "Cooking Equipment", "Other Equipment",
    ],
    "Hygiene": [
        "Personal Hygiene", "Sanitation", "Cleaning Supplies", "Other Hygiene",
    ],
    "First Aid": [
        "First Aid Kit", "Dressings and Bandages", "Antiseptic", "Other First Aid",
    ],
    "Medicines": [
        "Pain Relief", "Allergy Relief", "Cold and Flu",
        "Regular Medication", "Other Medicines",
    ],
    "Shelter and Warmth": [
        "Bedding", "Blankets", "Weather Protection", "Other Shelter and Warmth",
    ],
    "Communications and Power": [
        "Radio", "Power Banks", "Charging", "Other Communications and Power",
    ],
    "Pet Supplies": ["Pet Food"],
}


def build_import_template():
    """Build the branded PrepPi stock-import workbook in memory."""
    workbook = Workbook()

    readme = workbook.active
    readme.title = "Read me"
    sheet = workbook.create_sheet("Stock Import")

    navy = "172B4D"
    blue = "2878B5"
    pale_blue = "EAF2F8"
    grey = "F2F4F7"
    white = "FFFFFF"

    # Read me sheet
    readme.sheet_view.showGridLines = False
    readme.column_dimensions["A"].width = 5
    readme.column_dimensions["B"].width = 27
    readme.column_dimensions["C"].width = 85

    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH))
        logo.width = 115
        logo.height = 115
        readme.add_image(logo, "B2")

    readme.merge_cells("B9:C9")
    readme["B9"] = "PREPPI STOCK IMPORT TEMPLATE"
    readme["B9"].font = Font(size=18, bold=True, color=navy)
    readme["B9"].alignment = Alignment(vertical="center")
    readme.row_dimensions[9].height = 32

    instructions = [
        ("Purpose", "Use this workbook to add or update recorded household stock."),
        ("Required fields", "Item, Category, Sub-category and Qty must be completed."),
        ("Expiry date", "Use YYYY-MM-DD. Leave blank if the item has no expiry date or it is unknown."),
        ("Qty", "Enter the number of packs, tins, bottles or other units. It must be a positive whole number."),
        ("Unit size (g)", "Optional for ordinary stock. Enter the weight in grams for each pack where known."),
        ("Pet food", "Pet Supplies → Pet Food requires Unit size (g). Enter the weight of one bag, pouch or tin in grams."),
        ("Updating stock", "Use the same item name and category to update an existing entry. Qty is the number being added or removed, depending on the import action."),
        ("Compatibility", "The importer also accepts older five-column spreadsheets. This template uses the extended six-column format."),
        ("Import", "Complete the Stock Import sheet from row 5 onwards. Do not change the headings in row 4."),
    ]

    for row_number, (label, description) in enumerate(instructions, start=11):
        readme.cell(row_number, 2, label)
        readme.cell(row_number, 2).font = Font(bold=True, color=navy)
        readme.cell(row_number, 2).fill = PatternFill("solid", fgColor=pale_blue)
        readme.cell(row_number, 3, description)
        readme.cell(row_number, 3).alignment = Alignment(wrap_text=True, vertical="top")
        readme.row_dimensions[row_number].height = 32

    readme.freeze_panes = "B11"

    # Stock Import sheet
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A5"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True

    widths = [32, 27, 32, 18, 12, 20]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[
            chr(64 + index)
        ].width = width

    sheet.merge_cells("A1:F1")
    sheet["A1"] = "PREPPI — STOCK IMPORT"
    sheet["A1"].font = Font(size=18, bold=True, color=white)
    sheet["A1"].fill = PatternFill("solid", fgColor=navy)
    sheet["A1"].alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 36

    sheet.merge_cells("A2:F2")
    sheet["A2"] = (
        "Enter one stock item per row, beginning on row 5. "
        "Do not edit the column headings."
    )
    sheet["A2"].font = Font(italic=True, color=navy)
    sheet["A2"].fill = PatternFill("solid", fgColor=pale_blue)
    sheet["A2"].alignment = Alignment(wrap_text=True)
    sheet.row_dimensions[2].height = 28

    sheet.merge_cells("A3:F3")
    sheet["A3"] = (
        "Pet food must include its pack weight in grams in the final column."
    )
    sheet["A3"].font = Font(bold=True, color="9C5700")
    sheet["A3"].fill = PatternFill("solid", fgColor="FFF2CC")

    for column, heading in enumerate(HEADERS, start=1):
        cell = sheet.cell(4, column, heading)
        cell.font = Font(bold=True, color=white)
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(
            bottom=Side(style="medium", color=navy)
        )

    sheet.row_dimensions[4].height = 28

    # Hidden lookup sheet for Excel drop-downs
    lookup = workbook.create_sheet("_Lists")
    lookup.sheet_state = "hidden"

    for column, category in enumerate(CATEGORIES, start=1):
        column_letter = chr(64 + column)
        lookup.cell(1, column, category)

        for row, subcategory in enumerate(CATEGORIES[category], start=2):
            lookup.cell(row, column, subcategory)

        range_name = category.replace(" ", "_")
        workbook.defined_names.add(
            DefinedName(
                range_name,
                attr_text=(
                    f"'_Lists'!${column_letter}$2:"
                    f"${column_letter}${len(CATEGORIES[category]) + 1}"
                ),
            )
        )

    # Named range for the category drop-down
    workbook.defined_names.add(
        DefinedName(
            "CategoryList",
            attr_text="'_Lists'!$A$1:$I$1",
        )
    )

    category_dv = DataValidation(
        type="list",
        formula1="=CategoryList",
        allow_blank=True,
    )
    category_dv.error = "Choose a category from the drop-down list."
    category_dv.errorTitle = "Invalid category"
    category_dv.showErrorMessage = True
    sheet.add_data_validation(category_dv)
    category_dv.add("B5:B504")

    # Subcategory list follows the category selected in column B
    subcategory_dv = DataValidation(
        type="list",
        formula1='=INDIRECT(SUBSTITUTE($B5," ","_"))',
        allow_blank=True,
    )
    subcategory_dv.error = "Choose a sub-category for the selected category."
    subcategory_dv.errorTitle = "Invalid sub-category"
    subcategory_dv.showErrorMessage = True
    sheet.add_data_validation(subcategory_dv)
    subcategory_dv.add("C5:C504")

    # Basic cell formats and alternating row shading
    for row in range(5, 505):
        if row % 2 == 0:
            for column in range(1, 7):
                sheet.cell(row, column).fill = PatternFill(
                    "solid", fgColor=grey
                )
        sheet.cell(row, 4).number_format = "yyyy-mm-dd"
        sheet.cell(row, 5).number_format = "0"
        sheet.cell(row, 6).number_format = "0.##"

    sheet.auto_filter.ref = "A4:F504"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
