"""Per-browser sign-in; all authorized accounts share one portfolio."""
import time
import streamlit as st
from cloud_store import Api, CloudError

def require_login(config):
    if not isinstance(config.get('allowed_emails'), (list,tuple)):
        st.error('Set allowed_emails as a list in Streamlit Secrets.');st.stop()
    allowed={str(x).strip().lower() for x in config['allowed_emails']}
    if not allowed:st.error('Add at least one approved email in Streamlit Secrets.');st.stop()
    api=Api(config['url'],config['publishable_key'])
    token=st.session_state.get('_portfolio_token')
    if token:
        try:
            user=Api(config['url'],config['publishable_key'],token).request('GET','/auth/v1/user')
            email=(user.get('email') or '').lower()
            if email in allowed:
                st.sidebar.caption('Signed in as '+email)
                if st.sidebar.button('Sign out'):
                    try:Api(config['url'],config['publishable_key'],token).request('POST','/auth/v1/logout',{})
                    except CloudError:pass
                    st.session_state.clear();st.rerun()
                return
        except CloudError:pass
        st.session_state.pop('_portfolio_token',None)
    st.subheader('Sign in to your portfolio')
    st.caption('Use an approved Supabase account. All approved users can edit this shared portfolio.')
    with st.form('portfolio_login',clear_on_submit=True):
        email=st.text_input('Email address').strip().lower()
        password=st.text_input('Password',type='password')
        submit=st.form_submit_button('Sign in',type='primary')
    if submit:
        if time.time()<st.session_state.get('_portfolio_next_login',0):
            st.error('Please wait a few seconds before trying again.')
        else:
            st.session_state['_portfolio_next_login']=time.time()+3
            try:
                if email not in allowed:raise CloudError('Not approved')
                session=api.request('POST','/auth/v1/token?grant_type=password',{'email':email,'password':password})
                token=session.get('access_token')
                if not token:raise CloudError('No session')
                verified=Api(config['url'],config['publishable_key'],token).request('GET','/auth/v1/user')
                if (verified.get('email') or '').lower() not in allowed:raise CloudError('Not approved')
                st.session_state['_portfolio_token']=token
                st.rerun()
            except CloudError:
                st.error('Sign-in failed. Check your approved email and password, or ask the portfolio owner.')
    st.stop()
