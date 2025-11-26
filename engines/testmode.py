import streamlit as st
import time
from datetime import datetime, timedelta

# Initialize a persistent state to track last API call
def init_session_state():
    if 'last_api_call' not in st.session_state:
        st.session_state['last_api_call'] = None
    if 'call_count' not in st.session_state:
        st.session_state['call_count'] = 0

init_session_state()

# Function to perform rate-limited API call
def rate_limited_api_call():
    current_time = datetime.now()

    if st.session_state['call_count'] < 3:
        if st.session_state['last_api_call'] is None or \
           (current_time - st.session_state['last_api_call']) >= timedelta(hours=1):

            # Replace this with your actual API call
            st.write(f"Performing API call #{st.session_state['call_count'] + 1} at {current_time}")

            # Update session state
            st.session_state['last_api_call'] = current_time
            st.session_state['call_count'] += 1
        else:
            next_call_time = st.session_state['last_api_call'] + timedelta(hours=1)
            st.write(f"Next API call scheduled at {next_call_time.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        st.write("Maximum of 3 API calls reached for the current session.")

# Streamlit UI
st.title('⏳ Rate-Limited API Call')

if st.button('Start API Calls'):
    rate_limited_api_call()

# Auto-refresh every minute to check status (optional)
st_autorefresh = st.checkbox('Enable auto-refresh (every minute)', value=False)
if st_autorefresh:
    st.experimental_rerun()
