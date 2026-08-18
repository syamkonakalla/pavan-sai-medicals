import streamlit as st
import pandas as pd
import google.generativeai as genai
from supabase import create_client
from PIL import Image
from fpdf import FPDF
from datetime import datetime
import json
import requests
import base64
import io

st.set_page_config(page_title="Pharmacy Inventory Scanner", page_icon="💊", layout="wide")

st.title("💊 Pavan Sai Medicals — Inventory & Billing")
st.markdown("Scan wholesale invoices to build inventory in **Supabase**, then bill customers against live stock.")

# ==========================================
# SIDEBAR SETTINGS
# ==========================================
gemini_api_key = st.secrets["GEMINI_API_KEY"]
supabase_url = st.secrets["SUPABASE_URL"]
supabase_key = st.secrets["SUPABASE_KEY"]
apps_script_url = st.secrets["APPS_SCRIPT_URL"]

with st.sidebar:
    st.header("⚙️ Configuration")

    upsert_rule = st.radio(
        "When an existing product + batch is found:",
        ["Add to existing Quantity", "Overwrite Quantity"]
    )

if not gemini_api_key or not supabase_url or not supabase_key or not apps_script_url:
    st.info("👈 Please enter your **Gemini API Key**, **Supabase credentials**, and **Apps Script Web App URL** in the sidebar to begin.")
    st.stop()

# Configure Gemini + Supabase
genai.configure(api_key=gemini_api_key)
supabase = create_client(supabase_url, supabase_key)

SYSTEM_PROMPT = """
You are an expert OCR and data extraction assistant specialized in pharmaceutical invoices.
Analyze the provided image of a medical invoice and extract the items table.
Return ONLY a valid JSON array of objects. Do not include markdown blocks like ```json.
Map the columns exactly to these keys:
- "Product Name" (string)
- "Pack" (string)
- "HSN" (string)
- "Batch" (string)
- "Exp" (string)
- "Company" (string)
- "Quantity" (number)
- "MRP" (number)

If a value is missing or unreadable, use null.
"""

scan_tab, bill_tab = st.tabs(["📷 Scan Invoice", "🧾 Billing"])

# ==========================================
# TAB 1: SCAN INVOICE & SYNC INVENTORY
# ==========================================
with scan_tab:
    uploaded_file = st.file_uploader("Upload Invoice Image (JPG, PNG)", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        col1, col2 = st.columns([1, 1])
        file_bytes = uploaded_file.getvalue()

        with col1:
            image = Image.open(io.BytesIO(file_bytes))
            st.image(image, caption="Uploaded Invoice", use_column_width=True)

        with col2:
            if st.button("🚀 Process & Sync", type="primary"):
                # Image archiving to Drive skipped for now — drive_url/drive_file_id left null
                drive_url = None
                drive_file_id = None

                # 1. OCR Extraction via Gemini
                with st.spinner("1/2: Extracting data with Gemini AI..."):
                    try:
                        model = genai.GenerativeModel('gemini-3.6-flash')
                        response = model.generate_content([SYSTEM_PROMPT, image])

                        raw_text = response.text.strip()
                        if raw_text.startswith("```json"):
                            raw_text = raw_text[7:-3].strip()
                        elif raw_text.startswith("```"):
                            raw_text = raw_text[3:-3].strip()

                        scanned_items = json.loads(raw_text)
                        df_scanned = pd.DataFrame(scanned_items)
                    except Exception as e:
                        st.error(f"Error during AI extraction: {e}")
                        st.stop()

                st.write("**Extracted Items from Invoice:**")
                st.dataframe(df_scanned, height=180)

                # 2. Sync to Supabase
                with st.spinner("2/2: Updating Supabase inventory..."):
                    try:
                        invoice_res = supabase.table("invoices").insert({
                            "drive_file_id": drive_file_id,
                            "drive_url": drive_url,
                        }).execute()
                        invoice_id = invoice_res.data[0]["id"]

                        add_qty = (upsert_rule == "Add to existing Quantity")

                        for item in scanned_items:
                            product_name = item.get("Product Name")
                            batch = item.get("Batch")
                            quantity = item.get("Quantity") or 0

                            existing = (
                                supabase.table("inventory")
                                .select("id, quantity")
                                .eq("product_name", product_name)
                                .eq("batch", batch)
                                .execute()
                            )

                            if existing.data:
                                row_id = existing.data[0]["id"]
                                new_quantity = (
                                    existing.data[0]["quantity"] + quantity
                                    if add_qty else quantity
                                )
                                supabase.table("inventory").update({
                                    "quantity": new_quantity,
                                    "invoice_id": invoice_id,
                                }).eq("id", row_id).execute()
                            else:
                                supabase.table("inventory").insert({
                                    "invoice_id": invoice_id,
                                    "product_name": product_name,
                                    "pack": item.get("Pack"),
                                    "hsn": item.get("HSN"),
                                    "batch": batch,
                                    "exp": item.get("Exp"),
                                    "company": item.get("Company"),
                                    "quantity": quantity,
                                    "mrp": item.get("MRP"),
                                }).execute()

                        st.balloons()
                        st.success("✅ Supabase updated!")
                    except Exception as e:
                        st.error(f"Failed to sync to Supabase: {e}")
                        st.stop()

                # 3. Fetch and display latest live master inventory
                with st.spinner("Fetching updated live inventory..."):
                    try:
                        master_res = supabase.table("inventory").select("*").execute()
                        if master_res.data:
                            df_master = pd.DataFrame(master_res.data)
                            st.subheader("📊 Live Master Inventory")
                            st.dataframe(df_master, use_container_width=True)
                    except Exception as e:
                        st.warning(f"Could not refresh table preview: {e}")

# ==========================================
# TAB 2: BILLING
# ==========================================
with bill_tab:
    st.header("🧾 New Bill")

    if "cart" not in st.session_state:
        st.session_state.cart = []

    search_term = st.text_input("Search medicine by name")

    if search_term:
        matches = (
            supabase.table("inventory")
            .select("*")
            .ilike("product_name", f"%{search_term}%")
            .execute()
        )
        if matches.data:
            for row in matches.data:
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    st.write(
                        f"**{row['product_name']}** — Batch {row['batch']} — "
                        f"Exp {row['exp']} — MRP ₹{row['mrp']} — Stock: {row['quantity']}"
                    )
                with c2:
                    qty = st.number_input(
                        "Qty (strips)", min_value=1, value=1, step=1,
                        key=f"qty_{row['id']}", label_visibility="collapsed"
                    )
                with c3:
                    if st.button("➕ Add", key=f"add_{row['id']}"):
                        if qty > (row["quantity"] or 0):
                            st.error(f"Only {row['quantity']} in stock.")
                        else:
                            st.session_state.cart.append({
                                "inventory_id": row["id"],
                                "product_name": row["product_name"],
                                "batch": row["batch"],
                                "exp": row["exp"],
                                "mrp": row["mrp"],
                                "quantity": qty,
                            })
                            st.rerun()
        else:
            st.info("No matching medicine found.")

    if st.session_state.cart:
        st.subheader("Current Bill")
        cart_df = pd.DataFrame(st.session_state.cart)
        cart_df["amount"] = cart_df["mrp"] * cart_df["quantity"]
        st.dataframe(
            cart_df[["product_name", "batch", "exp", "mrp", "quantity", "amount"]],
            use_container_width=True
        )
        total = cart_df["amount"].sum()
        st.write(f"**Total: ₹{total:.2f}**")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("🗑️ Clear Bill"):
                st.session_state.cart = []
                st.rerun()
        with col_b:
            if st.button("✅ Generate Bill & Save to Drive", type="primary"):
                with st.spinner("Deducting stock and saving bill to Drive..."):
                    try:
                        # Build PDF bill
                        pdf = FPDF()
                        pdf.add_page()
                        pdf.set_font("Helvetica", "B", 16)
                        pdf.cell(0, 10, "PAVAN SAI MEDICALS", ln=True, align="C")
                        pdf.set_font("Helvetica", "", 10)
                        pdf.cell(0, 8, f"Bill Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}", ln=True, align="C")
                        pdf.ln(5)

                        col_widths = [55, 30, 30, 25, 20, 30]
                        headers = ["Product Name", "Batch", "Exp", "MRP", "Qty", "Amount"]

                        pdf.set_font("Helvetica", "B", 10)
                        for w, h in zip(col_widths, headers):
                            pdf.cell(w, 8, h, border=1)
                        pdf.ln()

                        pdf.set_font("Helvetica", "", 10)
                        for item in st.session_state.cart:
                            amount = item["mrp"] * item["quantity"]
                            pdf.cell(col_widths[0], 8, str(item["product_name"])[:30], border=1)
                            pdf.cell(col_widths[1], 8, str(item["batch"]), border=1)
                            pdf.cell(col_widths[2], 8, str(item["exp"]), border=1)
                            pdf.cell(col_widths[3], 8, f"{item['mrp']:.2f}", border=1)
                            pdf.cell(col_widths[4], 8, str(item["quantity"]), border=1)
                            pdf.cell(col_widths[5], 8, f"{amount:.2f}", border=1)
                            pdf.ln()

                        pdf.set_font("Helvetica", "B", 10)
                        pdf.cell(sum(col_widths[:-1]), 8, "Total", border=1)
                        pdf.cell(col_widths[-1], 8, f"{total:.2f}", border=1)
                        pdf.ln()

                        pdf_bytes = bytes(pdf.output())

                        # Upload PDF to Drive via existing Apps Script endpoint
                        bill_filename = f"Bill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                        drive_payload = {
                            "filename": bill_filename,
                            "mimeType": "application/pdf",
                            "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                        }
                        drive_res = requests.post(
                            apps_script_url.strip(),
                            data=json.dumps(drive_payload),
                            headers={"Content-Type": "text/plain;charset=utf-8"},
                        )
                        drive_json = json.loads(drive_res.text.strip())
                        if drive_json.get("status") != "success":
                            st.error(f"Drive upload failed: {drive_json.get('message')}")
                            st.stop()

                        # Deduct sold quantity from stock only after the bill is safely saved
                        for item in st.session_state.cart:
                            current = (
                                supabase.table("inventory")
                                .select("quantity")
                                .eq("id", item["inventory_id"])
                                .execute()
                            )
                            current_qty = current.data[0]["quantity"] or 0
                            supabase.table("inventory").update({
                                "quantity": current_qty - item["quantity"]
                            }).eq("id", item["inventory_id"]).execute()

                        st.balloons()
                        st.success(f"✅ Bill saved to Drive: {drive_json['url']}")
                        st.session_state.cart = []
                    except Exception as e:
                        st.error(f"Failed to generate bill: {e}")
