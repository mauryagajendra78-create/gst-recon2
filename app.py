from flask import Flask, request, render_template, send_file
import pandas as pd
import numpy as np
import os
import re

app = Flask(__name__)
UPLOAD_FOLDER = 'upload'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

temp_results = {}

def clean(df):
    df.columns = df.columns.astype(str).str.strip()
    return df.fillna("")

def find_column(df, options):
    for col in df.columns:
        for opt in options:
            if opt and opt.lower().strip() in str(col).lower().strip():
                return col
    return None

def find_header_row(df):
    for i in range(min(5, len(df))):
        row = df.iloc[i].astype(str).str.lower()
        if 'gstin' in row.values and 'invoice' in row.values:
            return i
    return 0

def normalize_invoice(inv):
    inv = str(inv).upper().strip()
    inv = re.sub(r'^(OS_|AMC|INV|BILL|GST|TAX|CN|DN)', '', inv)
    inv = re.sub(r'[\s/_\-\\]+', '', inv)
    inv = ''.join(filter(str.isalnum, inv))
    match = re.search(r'(\d{6,})$', inv)
    if match:
        inv = match.group(1)
    return inv

def extract_last_digits(inv):
    match = re.search(r'(\d{6,})$', str(inv))
    return match.group(1) if match else ''

def reconcile(gstr2b_raw, purchase_raw):
    if gstr2b_raw.empty or purchase_raw.empty:
        raise ValueError("One of the uploaded Excel sheets is empty.")

    header_row_2b = find_header_row(gstr2b_raw)
    header_row_book = find_header_row(purchase_raw)

    gstr2b_raw.columns = gstr2b_raw.iloc[header_row_2b]
    purchase_raw.columns = purchase_raw.iloc[header_row_book]
    gstr2b = clean(gstr2b_raw.iloc[header_row_2b + 1:].reset_index(drop=True))
    purchase = clean(purchase_raw.iloc[header_row_book + 1:].reset_index(drop=True))

    gstin_2b = find_column(gstr2b, ['gstin', 'gstin no', 'gstin_number'])
    inv_2b = find_column(gstr2b, ['invoice no', 'invoice', 'inv'])
    gstin_book = find_column(purchase, ['gstin', 'gstin no', 'gstin_number'])
    inv_book = find_column(purchase, ['invoice no', 'invoice', 'inv'])

    val_col = find_column(gstr2b, ['total value', 'value', 'taxable value', 'amount'])
    date_col = find_column(gstr2b, ['invoice date', 'date'])
    name_col = find_column(gstr2b, ['supplier name', 'name', 'party name'])

    if not all([gstin_2b, inv_2b, gstin_book, inv_book, val_col, date_col, name_col]):
        raise ValueError("Required columns not found in one of the files. Check headers.")

    gstr2b['normalized_inv'] = gstr2b[inv_2b].apply(normalize_invoice)
    purchase['normalized_inv'] = purchase[inv_book].apply(normalize_invoice)

    gstr2b['inv_tail'] = gstr2b[inv_2b].apply(extract_last_digits)
    purchase['inv_tail'] = purchase[inv_book].apply(extract_last_digits)

    gstr2b = gstr2b[gstr2b['normalized_inv'] != ""].copy().reset_index(drop=True)
    purchase = purchase[purchase['normalized_inv'] != ""].copy().reset_index(drop=True)

    gstr2b[gstin_2b] = gstr2b[gstin_2b].astype(str).str.lower().str.strip()
    purchase[gstin_book] = purchase[gstin_book].astype(str).str.lower().str.strip()
    gstr2b[name_col] = gstr2b[name_col].astype(str).str.lower().str.strip()
    purchase[name_col] = purchase[name_col].astype(str).str.lower().str.strip()

    gstr2b[val_col] = pd.to_numeric(gstr2b[val_col], errors='coerce')
    purchase[val_col] = pd.to_numeric(purchase[val_col], errors='coerce')

    gstr2b[date_col] = pd.to_datetime(gstr2b[date_col], errors='coerce', dayfirst=True)
    purchase[date_col] = pd.to_datetime(purchase[date_col], errors='coerce', dayfirst=True)

    gstr2b['is_duplicate'] = gstr2b.duplicated(subset=['normalized_inv', val_col, date_col, gstin_2b], keep=False)
    purchase['is_duplicate'] = purchase.duplicated(subset=['normalized_inv', val_col, date_col, gstin_book], keep=False)

    not_in_2b = purchase.merge(gstr2b[['normalized_inv']], on='normalized_inv', how='left', indicator=True).query('_merge == "left_only"').copy().reset_index(drop=True)
    not_in_books = gstr2b.merge(purchase[['normalized_inv']], on='normalized_inv', how='left', indicator=True).query('_merge == "left_only"').copy().reset_index(drop=True)

    merged = gstr2b.merge(purchase, on='normalized_inv', suffixes=('_2b', '_book'))


































    # ---------------- Part 2 ----------------
    partial_merge = gstr2b.merge(purchase, on='inv_tail', suffixes=('_2b', '_book'))
    tolerance = 2  # rupees ka round-off tolerance

    # vectorized match count with tolerance for values
    merged["match_count"] = (
        (merged[f"{inv_2b}_2b"].astype(str).str.strip() == merged[f"{inv_book}_book"].astype(str).str.strip()).astype(int)
        + (np.isclose(merged[f"{val_col}_2b"], merged[f"{val_col}_book"], atol=tolerance, equal_nan=True)).astype(int)
        + (merged[f"{date_col}_2b"] == merged[f"{date_col}_book"]).astype(int)
        + (merged[f"{gstin_2b}_2b"].astype(str).str.strip() == merged[f"{gstin_book}_book"].astype(str).str.strip()).astype(int)
        + (merged[f"{name_col}_2b"].astype(str).str.strip() == merged[f"{name_col}_book"].astype(str).str.strip()).astype(int)
    )

    matched = merged[merged["match_count"] == 5].copy()
    mismatched = merged[merged["match_count"] == 4].copy()

    def get_mismatch_reason(row):
        reasons = []
        if str(row[f"{inv_2b}_2b"]).strip() != str(row[f"{inv_book}_book"]).strip():
            reasons.append("Invoice format mismatch")
        if not np.isclose(row[f"{val_col}_2b"], row[f"{val_col}_book"], atol=tolerance, equal_nan=True):
            reasons.append("Value mismatch (> tolerance)")
        if row[f"{date_col}_2b"] != row[f"{date_col}_book"]:
            reasons.append("Date mismatch")
        if str(row[f"{gstin_2b}_2b"]).strip() != str(row[f"{gstin_book}_book"]).strip():
            reasons.append("GSTIN mismatch")
        if str(row[f"{name_col}_2b"]).strip() != str(row[f"{name_col}_book"]).strip():
            reasons.append("Supplier mismatch")
        return ", ".join(reasons)

    if not mismatched.empty:
        mismatched["Mismatch Reason"] = mismatched.apply(get_mismatch_reason, axis=1)
        mismatched['Duplicate Entry'] = mismatched.duplicated(
            subset=['normalized_inv', f"{val_col}_2b", f"{date_col}_2b", f"{gstin_2b}_2b"],
            keep=False
        ).apply(lambda x: 'Yes' if x else '')
    else:
        mismatched["Mismatch Reason"] = pd.Series(dtype=str)
        mismatched['Duplicate Entry'] = pd.Series(dtype=str)

    # vectorized strict partial match
    partial_invoice_match = partial_merge[
        (partial_merge[f"{gstin_2b}_2b"].astype(str).str.strip() == partial_merge[f"{gstin_book}_book"].astype(str).str.strip())
        & (partial_merge[f"{date_col}_2b"] == partial_merge[f"{date_col}_book"])
        & (np.isclose(partial_merge[f"{val_col}_2b"], partial_merge[f"{val_col}_book"], atol=tolerance, equal_nan=True))
        & (partial_merge[f"{name_col}_2b"].astype(str).str.strip() == partial_merge[f"{name_col}_book"].astype(str).str.strip())
        & (partial_merge['normalized_inv_2b'] != partial_merge['normalized_inv_book'])
    ].copy()

    # ✅ Book-side unique mapping: keep only one match per Book invoice
    partial_invoice_match = (
        partial_invoice_match
        .sort_values(by=[f"{date_col}_book", f"{val_col}_book"])  # optional sort
        .groupby(['normalized_inv_book'])
        .first()
        .reset_index()
    )

    partial_keys_2b = set(partial_invoice_match['normalized_inv_2b'].dropna().astype(str))
    partial_keys_book = set(partial_invoice_match['normalized_inv_book'].dropna().astype(str))

    not_in_books = not_in_books[~not_in_books['normalized_inv'].astype(str).isin(partial_keys_2b)].copy()
    not_in_2b = not_in_2b[~not_in_2b['normalized_inv'].astype(str).isin(partial_keys_book)].copy()
    mismatched = mismatched[~mismatched['normalized_inv'].astype(str).isin(partial_keys_2b)].copy()
    matched = matched[~matched['normalized_inv'].astype(str).isin(partial_keys_2b)].copy()

    return not_in_2b, not_in_books, mismatched, partial_invoice_match, matched





































@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    try:
        gstr2b_file = request.files['gstr2b']
        purchase_file = request.files['purchase']

        gstr2b_raw = pd.read_excel(gstr2b_file, header=None)
        purchase_raw = pd.read_excel(purchase_file, header=None)

        # total entry counts (after header rows)
        header_row_2b = find_header_row(gstr2b_raw)
        header_row_book = find_header_row(purchase_raw)
        total_2b_entries = max(0, len(gstr2b_raw) - (header_row_2b + 1))
        total_book_entries = max(0, len(purchase_raw) - (header_row_book + 1))

        not_in_2b, not_in_books, mismatched, partial_invoice_match, matched = reconcile(gstr2b_raw, purchase_raw)

        def clean_display(df):
            drop_cols = [col for col in df.columns if 'normalized_inv' in col or 'inv_tail' in col or '_merge' in col]
            df = df.drop(columns=drop_cols, errors='ignore')
            if 'Duplicate Entry' in df.columns:
                df['Duplicate Entry'] = df['Duplicate Entry'].apply(
                    lambda x: '<span class="duplicate-cell">Yes</span>' if x == 'Yes' else ''
                )
            return df.to_html(classes='table table-bordered', index=False, escape=False)

        temp_results['not_in_2b'] = not_in_2b
        temp_results['not_in_books'] = not_in_books
        temp_results['mismatched'] = mismatched
        temp_results['partial_invoice_match'] = partial_invoice_match
        temp_results['matched'] = matched

        return render_template('results.html',
            not_in_2b=clean_display(not_in_2b),
            not_in_books=clean_display(not_in_books),
            mismatched=clean_display(mismatched),
            partial_invoice_match=clean_display(partial_invoice_match),
            matched=clean_display(matched),
            count_2b=len(not_in_2b),
            count_books=len(not_in_books),
            count_mismatch=len(mismatched),
            count_partial=len(partial_invoice_match),
            count_matched=len(matched),
            total_2b_entries=total_2b_entries,
            total_book_entries=total_book_entries
        )
    except ValueError as ve:
        return f"⚠️ Format error: {ve}. Please check if headers like GSTIN, Invoice, Date, Value exist."
    except pd.errors.EmptyDataError:
        return "⚠️ Excel file is empty or corrupted. Please upload a valid sheet."
    except Exception as e:
        if "dtype" in str(e).lower():
            return "⚠️ Excel parsing error: Mixed or invalid data types found. Please clean the sheet."
        return f"⚠️ Unexpected error: {str(e)}. Please check your sheet content."


@app.route('/save')
def save_files():
    try:
        def save_both_formats(df, name):
            df = df.copy()
            drop_cols = [col for col in df.columns if 'normalized_inv' in col or 'inv_tail' in col or '_merge' in col]
            df = df.drop(columns=drop_cols, errors='ignore')
            df.to_csv(os.path.join(UPLOAD_FOLDER, f"{name}.csv"), index=False)
            df.to_excel(os.path.join(UPLOAD_FOLDER, f"{name}.xlsx"), index=False)

        save_both_formats(temp_results.get('not_in_2b', pd.DataFrame()), "not_in_2b")
        save_both_formats(temp_results.get('not_in_books', pd.DataFrame()), "not_in_books")
        save_both_formats(temp_results.get('mismatched', pd.DataFrame()), "mismatched")
        save_both_formats(temp_results.get('partial_invoice_match', pd.DataFrame()), "partial_invoice_match")
        save_both_formats(temp_results.get('matched', pd.DataFrame()), "matched")

        return render_template('saved.html')
    except Exception as e:
        return f"⚠️ Error while saving: {str(e)}"


@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename), as_attachment=True)


# ✅ Server start
if __name__ == '__main__':
    app.run(debug=True)