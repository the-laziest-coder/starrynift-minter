import requests
import ua_generator
from web3 import Web3
from config import RPCs
from vars import CONTRACTS_ADDRESS, CONTRACTS_ABI, ERC20_TOKEN_ABI, CHAIN_NAMES


def get_coin_price(coin, currency):
    resp = requests.get(
        f'https://api.coingecko.com/api/v3/coins/{coin}?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false&sparkline=false')
    return float(resp.json()['market_data']['current_price'][currency])


def get_w3(chain, proxy=None):
    req_args = {} if proxy is None or proxy == '' else {
        'proxies': {'https': proxy, 'http': proxy},
    }
    return Web3(Web3.HTTPProvider(RPCs[chain], request_kwargs=req_args))


def get_chain(w3):
    return CHAIN_NAMES[w3.eth.chain_id]


def get_contract(w3, contract_name, is_erc20_token=True):
    chain = get_chain(w3)
    abi = ERC20_TOKEN_ABI if is_erc20_token else CONTRACTS_ABI[chain][contract_name]
    return w3.eth.contract(CONTRACTS_ADDRESS[chain][contract_name], abi=abi)


def get_balance(w3, token, address, native=False):
    if native:
        return w3.eth.get_balance(address)
    contract = get_contract(w3, token)
    return contract.functions.balanceOf(address).call()


def send_tx(w3, private_key, tx, verify_func, action):
    estimate = w3.eth.estimate_gas(tx)
    tx['gas'] = estimate

    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    verify_func(get_chain(w3), tx_hash, action=action)

    return tx_hash


def build_and_send_tx(w3, address, private_key, func, value, verify_func, action):
    tx = func.build_transaction({
        'from': address,
        'nonce': w3.eth.get_transaction_count(address),
        'gasPrice': w3.eth.gas_price,
        'value': value,
    })

    return send_tx(w3, private_key, tx, verify_func, action)


def is_approved(w3, address, token, spender, amount):
    allowance = get_contract(w3, token).functions.allowance(address, spender).call()
    return amount <= allowance


def approve(w3, address, private_key, token, spender, verify_func):
    contract = get_contract(w3, token)
    build_and_send_tx(w3, address, private_key,
                      contract.functions.approve(spender, 2 ** 256 - 1), 0,
                      verify_func, 'Approve')


origin, sec_fetch_site, address2ua = 'https://starrynift.art', 'same-origin', {}


def get_default_headers(address):
    if address not in address2ua:
        address2ua[address] = ua_generator.generate(device='desktop', browser='chrome')
    ua = address2ua[address]
    return {
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'origin': origin,
        'referer': origin + '/',
        'sec-ch-ua': f'"{ua.ch.brands[2:]}"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': f'"{ua.platform.title()}"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': sec_fetch_site,
        'user-agent': ua.text,
    }
