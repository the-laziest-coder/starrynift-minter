import random
import time
import traceback
import web3.exceptions

from termcolor import cprint
from enum import Enum
from pathlib import Path
from datetime import datetime
from retry import retry
from eth_account.messages import encode_defunct
from eth_account.account import Account

from logger import Logger, get_telegram_bot_chat_id
from utils import *
from config import *
from vars import *

date_path = datetime.now().strftime('%d-%m-%Y-%H-%M-%S')
results_path = 'results/' + date_path
logs_root = 'logs/'
logs_path = logs_root + date_path
Path(results_path).mkdir(parents=True, exist_ok=True)
Path(logs_path).mkdir(parents=True, exist_ok=True)

logger = Logger(to_console=True, to_file=True, default_file=f'{logs_path}/console_output.txt')


def decimal_to_int(d, n):
    return int(d * (10 ** n))


def int_to_decimal(i, n):
    return i / (10 ** n)


def readable_amount_int(i, n, d=2):
    return round(int_to_decimal(i, n), d)


def wait_next_tx():
    time.sleep(random.uniform(NEXT_TX_MIN_WAIT_TIME, NEXT_TX_MAX_WAIT_TIME))


def _delay(r, *args, **kwargs):
    time.sleep(random.uniform(1, 2))


class RunnerException(Exception):

    def __init__(self, message, caused=None):
        super().__init__()
        self.message = message
        self.caused = caused

    def __str__(self):
        if self.caused:
            return self.message + ": " + str(self.caused)
        return self.message


class PendingException(Exception):

    def __init__(self, chain, tx_hash, action):
        super().__init__()
        self.chain = chain
        self.tx_hash = tx_hash
        self.action = action

    def __str__(self):
        return f'{self.action}, chain = {self.chain}, tx_hash = {self.tx_hash.hex()}'

    def get_tx_hash(self):
        return self.tx_hash.hex()


def handle_traceback(msg=''):
    trace = traceback.format_exc()
    logger.print(msg + '\n' + trace, filename=f'{logs_path}/tracebacks.log', to_console=False, store_tg=False)


def runner_func(msg):
    def decorator(func):
        @retry(tries=MAX_TRIES, delay=1.5, backoff=2, jitter=(0, 1), exceptions=RunnerException)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except PendingException:
                raise
            except RunnerException as e:
                raise RunnerException(msg, e)
            except Exception as e:
                handle_traceback(msg)
                raise RunnerException(msg, e)

        return wrapper

    return decorator


class Status(Enum):
    ALREADY = 1
    PENDING = 2
    SUCCESS = 3
    FAILED = 4


class Runner:
    STARRYNIFT_API_URL_V2 = 'https://api.starrynift.art/api-v2'

    def __init__(self, private_key, proxy):
        if proxy is not None and len(proxy) > 4 and proxy[:4] != 'http':
            proxy = 'http://' + proxy
        self.proxy = proxy

        self.w3s = {chain: get_w3(chain, proxy=self.proxy) for chain in INVOLVED_CHAINS}

        self.private_key = private_key
        self.address = Account().from_key(private_key).address

        self.sess = requests.Session()
        self.sess.proxies = {'http': self.proxy, 'https': self.proxy}
        self.sess.headers = get_default_headers(self.address)
        self.sess.hooks['response'].append(_delay)

    def w3(self, chain):
        return self.w3s[chain]

    def get_balance(self, chain, token, native=False):
        return get_balance(self.w3(chain), token, self.address, native=native)

    def get_contract(self, chain, contract):
        return get_contract(self.w3(chain), contract, is_erc20_token=False)

    @runner_func('Tx error')
    def tx_verification(self, chain, tx_hash, action=None):
        action_print = action + ' - ' if action else ''
        logger.print(f'{action_print}Tx was sent')
        try:
            transaction_data = self.w3(chain).eth.wait_for_transaction_receipt(tx_hash, timeout=TX_WAIT_TIME)
            status = transaction_data.get('status')
            if status is not None and status == 1:
                logger.print(f'{action_print}Successful tx: {SCANS[chain]}/tx/{tx_hash.hex()}')
            else:
                raise RunnerException(f'{action_print}Tx status = {status}, chain = {chain}, tx_hash = {tx_hash.hex()}')
        except web3.exceptions.TimeExhausted:
            raise PendingException(chain, tx_hash, action_print[:-3])

    def send_tx(self, w3, tx, action):
        return send_tx(w3, self.private_key, tx, self.tx_verification, action)

    @runner_func('Challenge')
    def challenge(self):
        resp_raw = self.sess.get(self.STARRYNIFT_API_URL_V2 +
                                 f'/starryverse/auth/wallet/challenge?address={self.address.lower()}')
        if resp_raw.status_code != 200:
            raise RunnerException(f'status_code = {resp_raw.status_code}, response = {resp_raw.text}')
        try:
            resp = resp_raw.json()
            return resp['message']
        except Exception:
            raise RunnerException(f'response = {resp_raw.text}')

    @runner_func('Login')
    def login(self, signature):
        resp_raw = self.sess.post(self.STARRYNIFT_API_URL_V2 + '/starryverse/auth/wallet/evm/login', json={
            'address': self.address.lower(),
            'referralSource': 0,
            'signature': signature,
        })
        if resp_raw.status_code != 200 and resp_raw.status_code != 201:
            raise RunnerException(f'status_code = {resp_raw.status_code}, response = {resp_raw.text}')
        try:
            resp = resp_raw.json()
            return resp['token']
        except Exception:
            raise RunnerException(f'response = {resp_raw.text}')

    @runner_func('Sign')
    def sign(self):
        resp_raw = self.sess.post(self.STARRYNIFT_API_URL_V2 + '/citizenship/citizenship-card/sign', json={
            'category': 1
        })
        if resp_raw.status_code != 201:
            raise RunnerException(f'status_code = {resp_raw.status_code}, response = {resp_raw.text}')
        try:
            return resp_raw.json()['signature']
        except Exception:
            raise RunnerException(f'response = {resp_raw.text}')

    @runner_func('Mint confirm')
    def mint_confirm(self, tx_hash):
        resp_raw = self.sess.post(self.STARRYNIFT_API_URL_V2 + '/webhook/confirm/citizenship/mint', json={
            'txHash': tx_hash
        })
        if resp_raw.status_code != 201:
            raise RunnerException(f'status_code = {resp_raw.status_code}, response = {resp_raw.text}')
        try:
            if resp_raw.json()['ok'] != 1:
                raise RunnerException(f'response = {resp_raw.text}')
        except Exception:
            raise RunnerException(f'response = {resp_raw.text}')

    @runner_func('Mint')
    def mint(self, signature):
        chain = 'BSC'
        w3 = self.w3(chain)

        data = '0xf75e0384' \
               '0000000000000000000000000000000000000000000000000000000000000020' \
               '000000000000000000000000' + self.address[2:].lower() + \
               '0000000000000000000000000000000000000000000000000000000000000001' \
               '0000000000000000000000000000000000000000000000000000000000000060' \
               '0000000000000000000000000000000000000000000000000000000000000041' + \
               signature[2:] + '00000000000000000000000000000000000000000000000000000000000000'

        gas_price = Web3.to_wei(round(random.uniform(MIN_GAS_PRICE, MAX_GAS_PRICE), 1), 'gwei')

        tx = {
            'chainId': w3.eth.chain_id,
            'to': CONTRACTS_ADDRESS[chain]['Starrynift'],
            'from': self.address,
            'nonce': w3.eth.get_transaction_count(self.address),
            'gasPrice': gas_price,
            'value': 0,
            'data': w3.to_bytes(hexstr=data),
        }

        return self.send_tx(w3, tx, 'Mint')

    def run(self):
        logger.print(self.address)

        message = self.challenge()

        wallet_sign = Account().sign_message(encode_defunct(text=message), self.private_key).signature.hex()

        token = self.login(wallet_sign)

        self.sess.headers.update({
            'Authorization': 'Bearer ' + token,
        })

        wait_next_tx()

        mint_signature = self.sign()

        try:
            tx_hash = self.mint(mint_signature)
        except RunnerException as e:
            if 'Card already minted for this category' in str(e):
                return Status.ALREADY
            else:
                raise

        self.mint_confirm(tx_hash.hex())

        return Status.SUCCESS


def wait_next_run(idx, runs_count):
    wait = random.randint(
        int(NEXT_ADDRESS_MIN_WAIT_TIME * 60),
        int(NEXT_ADDRESS_MAX_WAIT_TIME * 60)
    )

    done_msg = f'Done: {idx}/{runs_count}'
    waiting_msg = 'Waiting for next run for {:.2f} minutes'.format(wait / 60)

    cprint('\n#########################################\n#', 'cyan', end='')
    cprint(done_msg.center(39), 'magenta', end='')
    cprint('#\n#########################################', 'cyan', end='')

    tg_msg = done_msg

    cprint('\n# ', 'cyan', end='')
    cprint(waiting_msg, 'magenta', end='')
    cprint(' #\n#########################################\n', 'cyan')
    tg_msg += '. ' + waiting_msg

    logger.send_tg(tg_msg)

    time.sleep(wait)


def write_result(filename, account):
    with open(f'{results_path}/{filename}', 'a') as file:
        file.write(f'{"|".join([str(a) for a in list(account)])}\n')


def log_run(address, account, status, exc=None, msg=''):
    exc_msg = '' if exc is None else str(exc)

    account = (address,) + account

    if status == Status.ALREADY:
        summary_msg = 'Already minted'
        color = 'green'
        write_result('already.txt', account)
    elif status == Status.PENDING:
        summary_msg = 'Tx in pending: ' + exc_msg
        color = 'yellow'
        write_result('pending.txt', account)
    elif status == Status.SUCCESS:
        summary_msg = 'Run success'
        color = 'green'
        write_result('success.txt', account)
    else:
        summary_msg = 'Run failed: ' + exc_msg
        color = 'red'
        write_result('failed.txt', account)

    logger.print(summary_msg, color=color)

    if msg != '':
        logger.print(msg, color=color)

    logger.send_tg_stored()


def main():
    if GET_TELEGRAM_CHAT_ID:
        get_telegram_bot_chat_id()
        exit(0)

    random.seed(int(datetime.now().timestamp()))

    with open('files/wallets.txt', 'r', encoding='utf-8') as file:
        wallets = file.read().splitlines()
    with open('files/proxies.txt', 'r', encoding='utf-8') as file:
        proxies = file.read().splitlines()

    if len(proxies) == 0:
        proxies = [None] * len(wallets)
    if len(proxies) != len(wallets):
        cprint('Proxies count doesn\'t match wallets count. Add proxies or leave proxies file empty', 'red')
        return

    queue = list(zip(wallets, proxies))
    random.shuffle(queue)

    idx, runs_count = 0, len(queue)

    while len(queue) != 0:

        if idx != 0:
            wait_next_run(idx, runs_count)

        account = queue.pop(0)

        wallet, proxy = account

        if wallet.find(';') == -1:
            key = wallet
        else:
            key = wallet.split(';')[1]

        runner = Runner(key, proxy)

        address = runner.address

        exc = None

        try:
            status = runner.run()
        except PendingException as e:
            status = Status.PENDING
            exc = e
        except RunnerException as e:
            status = Status.FAILED
            exc = e
        except Exception as e:
            handle_traceback()
            status = Status.FAILED
            exc = e

        log_run(address, account, status, exc=exc)

        idx += 1

    cprint('\n#########################################\n#', 'cyan', end='')
    cprint(f'Finished'.center(39), 'magenta', end='')
    cprint('#\n#########################################', 'cyan')


if __name__ == '__main__':
    cprint('###########################################################', 'cyan')
    cprint('#######################', 'cyan', end='')
    cprint(' By @timfame ', 'magenta', end='')
    cprint('#######################', 'cyan')
    cprint('###########################################################\n', 'cyan')

    main()
