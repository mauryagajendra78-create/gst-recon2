import pandas as pd

def clean(df):
    df.columns = df.columns.astype(str).str.strip()
    return df.fillna("")

def find_column(df, options):
    for col in df.columns:
        for opt in options:
            if opt.lower().strip() in col.lower().strip():
                return col
    return None

def find_header_row(df):
    for i in range(min(5, len(df))):
        row = df.iloc[i].astype(str).str.lower()
        if 'gstin' in row.values and 'invoice' in row.values:
            return i
    return 0

def reconcile(gstr2b_raw, purchase_raw):
    header_row_2b = find_header_row(gstr2b_raw)
    header_row_book = find_header_row(purchase_raw)

    gstr2b_raw.columns = gstr2b_raw.iloc[header_row_2b]
    purchase_raw.columns = purchase_raw.iloc[header_row_book]
    gstr2b = clean(gstr2b_raw.iloc[header_row_2b + 1:].reset_index(drop=True))
    purchase = clean(purchase_raw.iloc[header_row_book + 1:].reset_index(drop=True))

    gstin_2b = find_column(gstr2b, ['GSTIN'])
    inv_2b = find_column(gstr2b, ['Invoice No'])
    gstin_book = find_column(purchase, ['GSTIN'])
    inv_book = find_column(purchase, ['Invoice No'])

    val_col = find_column(gstr2b, ['Total Value'])
    date_col = find_column(gstr2b, ['Invoice Date'])
    name_col = find_column(gstr2b, ['Supplier Name'])

    not_in_2b = purchase.merge(
        gstr2b[[gstin_2b, inv_2b]],
        left_on=[gstin_book, inv_book],
        right_on=[gstin_2b, inv_2b],
        how='left',
        indicator=True
    ).query('_merge == "left_only"')

    not_in_books = gstr2b.merge(
        purchase[[gstin_book, inv_book]],
        left_on=[gstin_2b, inv_2b],
        right_on=[gstin_book, inv_book],
        how='left',
        indicator=True
    ).query('_merge == "left_only"')

    merged = gstr2b.merge(
        purchase,
        left_on=[gstin_2b, inv_2b],
        right_on=[gstin_book, inv_book],
        suffixes=('_2b', '_book')
    )

    mismatched = merged[
        (merged[f"{val_col}_2b"] != merged[f"{val_col}_book"]) |
        (merged[f"{date_col}_2b"] != merged[f"{date_col}_book"]) |
        (merged[f"{name_col}_2b"] != merged[f"{name_col}_book"])
    ]

    return not_in_2b, not_in_books, mismatched

