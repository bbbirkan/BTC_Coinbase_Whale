
from math import log10, floor, isnan
from datetime import datetime
# non-dash-related libraries
import pandas as pd
import numpy as np
# modules added by contributors
import time
import threading
refreshes = 0
# class libary
from operator import itemgetter
from bintrees import RBTree
from decimal import Decimal
from cbpro.public_client import PublicClient
from cbpro.websocket_client import WebsocketClient
class GDaxBook(WebsocketClient):
    def __init__(self, product_id='BTC-USD'):
        # print("Initializing order Book websocket for " + product_id)
        self.product = product_id
        super(GDaxBook, self).__init__(url="wss://ws-feed.pro.coinbase.com", products=[self.product],
                                       channels=["ticker"])
        super(GDaxBook, self).start()
        self._asks = RBTree()
        self._bids = RBTree()
        self._client = PublicClient()
        self._sequence = -1
        self._current_ticker = None

    def on_message(self, message):
        sequence = message.get('sequence', -1)
        if self._sequence == -1:
            self._asks = RBTree()
            self._bids = RBTree()
            res = self._client.get_product_order_book(self.product, level=3)
            for bid in res['bids']:
                self.add({
                    'id': bid[2],
                    'side': 'buy',
                    'price': Decimal(bid[0]),
                    'size': Decimal(bid[1])
                })
            for ask in res['asks']:
                self.add({
                    'id': ask[2],
                    'side': 'sell',
                    'price': Decimal(ask[0]),
                    'size': Decimal(ask[1])
                })
            self._sequence = res['sequence']
        if sequence <= self._sequence:
            # ignore older messages (e.g. before order book initialization from getProductOrderBook)
            return
        # elif sequence > self._sequence + 1:
        #     print('Error: messages missing ({} - {}). Re-initializing websocket.'.format(sequence, self._sequence))
        #     self.close()
        #     self.start()
        #     return

        msg_type = message['type']
        if msg_type == 'open':
            self.add(message)
        elif msg_type == 'done' and 'price' in message:
            self.remove(message)
        elif msg_type == 'match':
            self.match(message)
            self._current_ticker = message
        elif msg_type == 'change':
            self.change(message)
        self._sequence = sequence
        # bid = self.get_bid()
        # bids = self.get_bids(bid)
        # bid_depth = sum([b['size'] for b in bids])
        # ask = self.get_ask()
        # asks = self.get_asks(ask)
        # ask_depth = sum([a['size'] for a in asks])
        # print('bid: %f @ %f - ask: %f @ %f' % (bid_depth, bid, ask_depth, ask))

    def on_error(self, e):
        self._sequence = -1
        self.close()
        self.start()

    def add(self, order):
        order = {
            'id': order.get('order_id') or order['id'],
            'side': order['side'],
            'price': Decimal(order['price']),
            'size': Decimal(order.get('size') or order['remaining_size'])
        }
        if order['side'] == 'buy':
            bids = self.get_bids(order['price'])
            if bids is None:
                bids = [order]
            else:
                bids.append(order)
            self.set_bids(order['price'], bids)
        else:
            asks = self.get_asks(order['price'])
            if asks is None:
                asks = [order]
            else:
                asks.append(order)
            self.set_asks(order['price'], asks)

    def remove(self, order):
        price = Decimal(order['price'])
        if order['side'] == 'buy':
            bids = self.get_bids(price)
            if bids is not None:
                bids = [o for o in bids if o['id'] != order['order_id']]
                if len(bids) > 0:
                    self.set_bids(price, bids)
                else:
                    self.remove_bids(price)
        else:
            asks = self.get_asks(price)
            if asks is not None:
                asks = [o for o in asks if o['id'] != order['order_id']]
                if len(asks) > 0:
                    self.set_asks(price, asks)
                else:
                    self.remove_asks(price)

    def match(self, order):
        size = Decimal(order['size'])
        price = Decimal(order['price'])

        if order['side'] == 'buy':
            bids = self.get_bids(price)
            if not bids:
                return
            assert bids[0]['id'] == order['maker_order_id']
            if bids[0]['size'] == size:
                self.set_bids(price, bids[1:])
            else:
                bids[0]['size'] -= size
                self.set_bids(price, bids)
        else:
            asks = self.get_asks(price)
            if not asks:
                return
            assert asks[0]['id'] == order['maker_order_id']
            if asks[0]['size'] == size:
                self.set_asks(price, asks[1:])
            else:
                asks[0]['size'] -= size
                self.set_asks(price, asks)

    def change(self, order):
        new_size = Decimal(order['new_size'])
        price = Decimal(order['price'])

        if order['side'] == 'buy':
            bids = self.get_bids(price)
            if bids is None or not any(o['id'] == order['order_id'] for o in bids):
                return
            index = map(itemgetter('id'), bids).index(order['order_id'])
            bids[index]['size'] = new_size
            self.set_bids(price, bids)
        else:
            asks = self.get_asks(price)
            if asks is None or not any(o['id'] == order['order_id'] for o in asks):
                return
            index = map(itemgetter('id'), asks).index(order['order_id'])
            asks[index]['size'] = new_size
            self.set_asks(price, asks)

        tree = self._asks if order['side'] == 'sell' else self._bids
        node = tree.get(price)

        if node is None or not any(o['id'] == order['order_id'] for o in node):
            return

    def get_current_ticker(self):
        return self._current_ticker

    def get_current_book(self):
        result = {
            'sequence': self._sequence,
            'asks': [],
            'bids': [],
        }
        for ask in self._asks:
            try:
                # There can be a race condition here, where a price point is removed
                # between these two ops
                this_ask = self._asks[ask]
            except KeyError:
                continue
            for order in this_ask:
                result['asks'].append([order['price'], order['size'], order['id']])
        for bid in self._bids:
            try:
                # There can be a race condition here, where a price point is removed
                # between these two ops
                this_bid = self._bids[bid]
            except KeyError:
                continue

            for order in this_bid:
                result['bids'].append([order['price'], order['size'], order['id']])
        return result

    def get_ask(self):
        return self._asks.min_key()

    def get_asks(self, price):
        return self._asks.get(price)

    def remove_asks(self, price):
        self._asks.remove(price)

    def set_asks(self, price, asks):
        self._asks.insert(price, asks)

    def get_bid(self):
        return self._bids.max_key()

    def get_bids(self, price):
        return self._bids.get(price)

    def remove_bids(self, price):
        self._bids.remove(price)

    def set_bids(self, price, bids):
        self._bids.insert(price, bids)
class Exchange:
    ticker = []
    client = ""

    def __init__(self, pName, pTicker, pStamp):
        self.name = pName
        self.ticker.extend(pTicker)
        self.millis = pStamp
class Pair:
    # Class to store a pair with its respective threads
    def __init__(self, pExchange, pTicker):
        self.ob_Inst = {}
        self.threadWebsocket = {}
        self.threadPrepare = {}
        self.threadRecalc = {}
        self.Dataprepared = False
        self.webSocketKill = 1
        self.lastStamp = 0
        self.usedStamp = 0
        self.newData = False
        self.name = pExchange + " " + pTicker
        self.ticker = pTicker
        self.lastUpdate = "0"
        self.exchange = pExchange
        self.prepare = False
        self.websocket = False
        self.combined = pExchange + pTicker
# extra
import os
import gspread
import requests

debugLevel = 3
debugLevels = ["Special Debug", "Debug", "Info", "Warnings", "Errors"]
debugColors = ['\033[34m', '\033[90m', '\033[32m', '\033[33;1m', '\033[31m']
serverPort = 8050
clientRefresh = 1
desiredPairRefresh = 10000  # (in ms) The lower it is, the better is it regarding speed of at least some pairs, the higher it is, the less cpu load it takes.
noDouble = True  # if activatet each order is in case of beeing part of a ladder just shown once (just as a bubble, not as a ladder)
SYMBOLS = {"USD": "$", "BTC": "₿", "EUR": "€", "GBP": "£"}  # used for the tooltip
SIGNIFICANT = {"USD": 2, "BTC": 5, "EUR": 2, "GBP": 2}  # used for rounding
TBL_PRICE = 'price'
TBL_VOLUME = 'volume'
tables = {}
depth_ask = {}
depth_bid = {}
marketPrice = {}
prepared = {}
shape_bid = {}
shape_ask = {}
timeStampsGet = {}  # For storing timestamp of Data Refresh
timeStamps = {}  # For storing timestamp from calc start at calc end
sendCache = {}
first_prepare = True
first_pull = True
overallNewData = False
stop_threads = False
PAIRS = []  # Array containing all pairs
#"BTC-GBP,BTC-USD-BTC-EUR"
E_GDAX = Exchange("Coinbase", ["BTC-EUR"], 0)
for ticker in E_GDAX.ticker:
    cObj = Pair(E_GDAX.name, ticker)
    PAIRS.append(cObj)
static_content_before = ""
cCache = []
for pair in PAIRS:
    ticker = pair.ticker
    exchange = pair.exchange
    graph = 'live-graph-' + exchange + "-" + ticker

def safe_num(num):
    if isinstance(num, str):
        num = float(num)
    return float('{:.3g}'.format(abs(num)))
def format_number(num):
    num = safe_num(num)
    sign = ''
    metric = {'T': 1000000000000, 'B': 1000000000, 'M': 1000000, 'K': 1000, '': 1}
    for index in metric:
        num_check = num / metric[index]
        if (num_check >= 1):
            num = num_check
            sign = index
            break
    return f"{str(num).rstrip('0').rstrip('.')}{sign}"
def get_data_cache(ticker):
    return tables[ticker]
def get_All_data():
    return prepared
def getSendCache():
    return sendCache
def prepare_send():
    lCache = []
    cData = get_All_data()
    for pair in PAIRS:
        ticker = pair.ticker
        exchange = pair.exchange
        graph = 'live-graph-' + exchange + "-" + ticker
    return
def calc_data(pair, range=0.05, maxSize=32, minVolumePerc=0.01, ob_points=60):
    global tables, timeStamps, shape_bid, shape_ask, E_GDAX, marketPrice, timeStampsGet, last_ask
    ticker = pair.ticker
    exchange = pair.exchange
    combined = exchange + ticker
    if pair.exchange == E_GDAX.name:
        # order_book = gdax.PublicClient().get_product_order_book(ticker, level=3)
        order_book = pair.ob_Inst.get_current_book()
        pair.usedStamp = getStamp()
        ask_tbl = pd.DataFrame(data=order_book['asks'], columns=[
            TBL_PRICE, TBL_VOLUME, 'address'])
        bid_tbl = pd.DataFrame(data=order_book['bids'], columns=[
            TBL_PRICE, TBL_VOLUME, 'address'])

    timeStampsGet[pair.combined] = datetime.now().strftime("%H:%M:%S")  # save timestamp at data pull time
    currency = ticker.split("-")[0]
    base_currency = ticker.split("-")[1]
    sig_use = SIGNIFICANT.get(base_currency.upper(), 2)
    symbol = SYMBOLS.get(base_currency.upper(), "")
    try:
        first_ask = float(ask_tbl.iloc[1, 0])
    except (IndexError):
        log(4, "Empty data for " + combined + " Will wait 1s")
        time.sleep(1)
        return False

    # prepare Price
    ask_tbl[TBL_PRICE] = pd.to_numeric(ask_tbl[TBL_PRICE])
    bid_tbl[TBL_PRICE] = pd.to_numeric(bid_tbl[TBL_PRICE])

    # data from websocket are not sorted yet
    ask_tbl = ask_tbl.sort_values(by=TBL_PRICE, ascending=True)
    bid_tbl = bid_tbl.sort_values(by=TBL_PRICE, ascending=False)

    # get first on each side
    first_ask = float(ask_tbl.iloc[1, 0])

    # get perc for ask/ bid
    perc_above_first_ask = ((1.0 + range) * first_ask)
    perc_above_first_bid = ((1.0 - range) * first_ask)

    # limits the size of the table so that we only look at orders 5% above and under market price
    ask_tbl = ask_tbl[(ask_tbl[TBL_PRICE] <= perc_above_first_ask)]
    bid_tbl = bid_tbl[(bid_tbl[TBL_PRICE] >= perc_above_first_bid)]

    # changing this position after first filter makes calc faster
    bid_tbl[TBL_VOLUME] = pd.to_numeric(bid_tbl[TBL_VOLUME])
    ask_tbl[TBL_VOLUME] = pd.to_numeric(ask_tbl[TBL_VOLUME])

    # prepare everything for depchart
    ob_step = (perc_above_first_ask - first_ask) / ob_points
    ob_ask = pd.DataFrame(columns=[TBL_PRICE, TBL_VOLUME, 'address', 'text'])
    ob_bid = pd.DataFrame(columns=[TBL_PRICE, TBL_VOLUME, 'address', 'text'])

    # Following is creating a new tbl 'ob_bid' which contains the summed volume and adress-count from current price to target price
    i = 1
    last_ask = first_ask
    last_bid = first_ask
    current_ask_volume = 0
    current_bid_volume = 0
    current_ask_adresses = 0
    current_bid_adresses = 0
    while i < ob_points:
        # Get Borders for ask/ bid
        current_ask_border = first_ask + (i * ob_step)
        current_bid_border = first_ask - (i * ob_step)

        # Get Volume
        current_ask_volume += ask_tbl.loc[
            (ask_tbl[TBL_PRICE] >= last_ask) & (ask_tbl[TBL_PRICE] < current_ask_border), TBL_VOLUME].sum()
        current_bid_volume += bid_tbl.loc[
            (bid_tbl[TBL_PRICE] <= last_bid) & (bid_tbl[TBL_PRICE] > current_bid_border), TBL_VOLUME].sum()

        # Get Adresses
        current_ask_adresses += ask_tbl.loc[
            (ask_tbl[TBL_PRICE] >= last_ask) & (ask_tbl[TBL_PRICE] < current_ask_border), 'address'].count()
        current_bid_adresses += bid_tbl.loc[
            (bid_tbl[TBL_PRICE] <= last_bid) & (bid_tbl[TBL_PRICE] > current_bid_border), 'address'].count()

        # Prepare Text
        ask_text = (str(round_sig(current_ask_volume, 3, 0, sig_use)) + currency + " (from " + str(
            current_ask_adresses) +
                    " orders) up to " + str(round_sig(current_ask_border, 3, 0, sig_use)) + symbol)
        bid_text = (str(round_sig(current_bid_volume, 3, 0, sig_use)) + currency + " (from " + str(
            current_bid_adresses) +
                    " orders) down to " + str(round_sig(current_bid_border, 3, 0, sig_use)) + symbol)

        # Save Data
        ob_ask.loc[i - 1] = [current_ask_border, current_ask_volume, current_ask_adresses, ask_text]
        ob_bid.loc[i - 1] = [current_bid_border, current_bid_volume, current_bid_adresses, bid_text]
        i += 1
        last_ask = current_ask_border
        last_bid = current_bid_border

    # Get Market Price
    try:
        mp = round_sig((ask_tbl[TBL_PRICE].iloc[0] +
                        bid_tbl[TBL_PRICE].iloc[0]) / 2.0, 3, 0, sig_use)
    except (IndexError):
        print("Empty data for " + combined + " Will wait 2s")
        time.sleep(2)
        return False
    bid_tbl = bid_tbl.iloc[::-1]  # flip the bid table so that the merged full_tbl is in logical order

    fulltbl = bid_tbl.append(ask_tbl)  # append the buy and sell side tables to create one cohesive table

    minVolume = fulltbl[TBL_VOLUME].sum() * minVolumePerc  # Calc minimum Volume for filtering
    fulltbl = fulltbl[
        (fulltbl[
             TBL_VOLUME] >= minVolume)]  # limit our view to only orders greater than or equal to the minVolume size

    fulltbl['sqrt'] = np.sqrt(fulltbl[
                                  TBL_VOLUME])  # takes the square root of the volume (to be used later on for the purpose of sizing the order bubbles)

    final_tbl = fulltbl.groupby([TBL_PRICE])[
        [TBL_VOLUME]].sum()  # transforms the table for a final time to craft the data view we need for analysis

    final_tbl['n_unique_orders'] = fulltbl.groupby(
        TBL_PRICE).address.nunique().astype(int)

    final_tbl = final_tbl[(final_tbl['n_unique_orders'] <= 20.0)]
    final_tbl[TBL_PRICE] = final_tbl.index
    final_tbl[TBL_PRICE] = final_tbl[TBL_PRICE].apply(round_sig, args=(3, 0, sig_use))
    final_tbl[TBL_VOLUME] = final_tbl[TBL_VOLUME].apply(round_sig, args=(1, 2))
    final_tbl['n_unique_orders'] = final_tbl['n_unique_orders'].apply(round_sig, args=(0,))
    final_tbl['sqrt'] = np.sqrt(final_tbl[TBL_VOLUME])
    final_tbl['total_price'] = (
        ((final_tbl['volume'] * final_tbl['price']).round(2)).apply(lambda x: "{:,}".format(x)))

    # Following lines fix double drawing of orders in case it´s a ladder but bigger than 1%
    if noDouble:
        bid_tbl = bid_tbl[(bid_tbl['volume'] < minVolume)]
        ask_tbl = ask_tbl[(ask_tbl['volume'] < minVolume)]

    bid_tbl['total_price'] = bid_tbl['volume'] * bid_tbl['price']
    ask_tbl['total_price'] = ask_tbl['volume'] * ask_tbl['price']

    # Get Dataset for Volume Grouping
    vol_grp_bid = bid_tbl.groupby([TBL_VOLUME]).agg(
        {TBL_PRICE: [np.min, np.max, 'count'], TBL_VOLUME: np.sum, 'total_price': np.sum})
    vol_grp_ask = ask_tbl.groupby([TBL_VOLUME]).agg(
        {TBL_PRICE: [np.min, np.max, 'count'], TBL_VOLUME: np.sum, 'total_price': np.sum})

    # Rename column names for Volume Grouping
    vol_grp_bid.columns = ['min_Price', 'max_Price', 'count', TBL_VOLUME, 'total_price']
    vol_grp_ask.columns = ['min_Price', 'max_Price', 'count', TBL_VOLUME, 'total_price']

    # Filter data by min Volume, more than 1 (intefere with bubble), less than 70 (mostly 1 or 0.5 ETH humans)
    vol_grp_bid = vol_grp_bid[
        ((vol_grp_bid[TBL_VOLUME] >= minVolume) & (vol_grp_bid['count'] >= 2.0) & (vol_grp_bid['count'] < 70.0))]
    vol_grp_ask = vol_grp_ask[
        ((vol_grp_ask[TBL_VOLUME] >= minVolume) & (vol_grp_ask['count'] >= 2.0) & (vol_grp_ask['count'] < 70.0))]

    # Get the size of each order
    vol_grp_bid['unique'] = vol_grp_bid.index.get_level_values(TBL_VOLUME)
    vol_grp_ask['unique'] = vol_grp_ask.index.get_level_values(TBL_VOLUME)

    # Round the size of order
    vol_grp_bid['unique'] = vol_grp_bid['unique'].apply(round_sig, args=(3, 0, sig_use))
    vol_grp_ask['unique'] = vol_grp_ask['unique'].apply(round_sig, args=(3, 0, sig_use))

    # Round the Volume
    vol_grp_bid[TBL_VOLUME] = vol_grp_bid[TBL_VOLUME].apply(round_sig, args=(1, 0, sig_use))
    vol_grp_ask[TBL_VOLUME] = vol_grp_ask[TBL_VOLUME].apply(round_sig, args=(1, 0, sig_use))

    # Round the Min/ Max Price
    vol_grp_bid['min_Price'] = vol_grp_bid['min_Price'].apply(round_sig, args=(3, 0, sig_use))
    vol_grp_ask['min_Price'] = vol_grp_ask['min_Price'].apply(round_sig, args=(3, 0, sig_use))
    vol_grp_bid['max_Price'] = vol_grp_bid['max_Price'].apply(round_sig, args=(3, 0, sig_use))
    vol_grp_ask['max_Price'] = vol_grp_ask['max_Price'].apply(round_sig, args=(3, 0, sig_use))

    # Round and format the Total Price
    vol_grp_bid['total_price'] = (vol_grp_bid['total_price'].round(sig_use).apply(lambda x: "{:,}".format(x)))
    vol_grp_ask['total_price'] = (vol_grp_ask['total_price'].round(sig_use).apply(lambda x: "{:,}".format(x)))

    # Append individual text to each element
    vol_grp_bid['text'] = ("There are " + vol_grp_bid['count'].map(str) + " orders " + vol_grp_bid['unique'].map(
        str) + " " + currency +
                           " each, from " + symbol + vol_grp_bid['min_Price'].map(str) + " to " + symbol +
                           vol_grp_bid['max_Price'].map(str) + " resulting in a total of " + vol_grp_bid[
                               TBL_VOLUME].map(str) + " " + currency + " worth " + symbol + vol_grp_bid[
                               'total_price'].map(str))
    vol_grp_ask['text'] = ("There are " + vol_grp_ask['count'].map(str) + " orders " + vol_grp_ask['unique'].map(
        str) + " " + currency +
                           " each, from " + symbol + vol_grp_ask['min_Price'].map(str) + " to " + symbol +
                           vol_grp_ask['max_Price'].map(str) + " resulting in a total of " + vol_grp_ask[
                               TBL_VOLUME].map(str) + " " + currency + " worth " + symbol + vol_grp_ask[
                               'total_price'].map(str))

    # Save data global
    shape_ask[combined] = vol_grp_ask
    shape_bid[combined] = vol_grp_bid

    cMaxSize = final_tbl['sqrt'].max()  # Fixing Bubble Size

    # nifty way of ensuring the size of the bubbles is proportional and reasonable
    sizeFactor = maxSize / cMaxSize
    final_tbl['sqrt'] = final_tbl['sqrt'] * sizeFactor

    # making the tooltip column for our charts
    final_tbl['text'] = (
            "There is a " + final_tbl[TBL_VOLUME].map(str) + " " + currency + " order for " + symbol + final_tbl[
        TBL_PRICE].map(str) + " being offered by " + final_tbl['n_unique_orders'].map(
        str) + " unique orders worth " + symbol + final_tbl['total_price'].map(str))

    # determine buys / sells relative to last market price; colors price bubbles based on size
    # Buys are green, Sells are Red. Probably WHALES are highlighted by being brighter, detected by unqiue order count.
    final_tbl['colorintensity'] = final_tbl['n_unique_orders'].apply(calcColor)
    final_tbl.loc[(final_tbl[TBL_PRICE] > mp), 'color'] = \
        'rgb(' + final_tbl.loc[(final_tbl[TBL_PRICE] >
                                mp), 'colorintensity'].map(str) + ',0,0)'
    final_tbl.loc[(final_tbl[TBL_PRICE] <= mp), 'color'] = \
        'rgb(0,' + final_tbl.loc[(final_tbl[TBL_PRICE]
                                  <= mp), 'colorintensity'].map(str) + ',0)'

    timeStamps[combined] = timeStampsGet[combined]  # now save timestamp of calc start in timestamp used for title

    tables[combined] = final_tbl  # save table data

    marketPrice[combined] = mp  # save market price

    depth_ask[combined] = ob_ask
    depth_bid[combined] = ob_bid

    pair.newData = True
    pair.prepare = True  # just used for first enabling of send prepare
    return True
def round_sig(x, sig=3, overwrite=0, minimum=0):
    if (x == 0):
        return 0.0
    elif overwrite > 0:
        return round(x, overwrite)
    else:
        digits = -int(floor(log10(abs(x)))) + (sig - 1)
        if digits <= minimum:
            return round(x, minimum)
        else:
            return round(x, digits)
def calcColor(x):
    response = round(400 / x)
    if response > 255:
        response = 255
    elif response < 30:
        response = 30
    return response
def fixNan(x, pMin=True):
    if isnan(x):
        if pMin:
            return 99999
        else:
            return 0
    else:
        return x
def getStamp():
    return int(round(time.time() * 1000))
def serverThread():
    pass
def update_Site_data():
    return getSendCache()
def sendPrepareThread():
    global sendCache, first_prepare, overallNewData, stop_threads
    while True:
        sendCache = prepare_send()
        overallNewData = False
        time.sleep(0.5)
def recalcThread(pair):
    global stop_threads, refreshes
    count = 0
    refreshes = 0
    while True:
        if (pair.websocket):
            dif = getStamp() - pair.lastStamp
            if dif > desiredPairRefresh:
                refreshes += 1
                print(pair.ticker + " Total refreshes for pair " + str(refreshes))
                if not calc_data(pair):
                    count = count + 1
                else:
                    count = 0
                    pair.lastStamp = pair.usedStamp
                    break
                if count > 5:
                    print("Going to kill Web socket from " + pair.ticker)
                    count = -1
                    pair.webSocketKill = 0
            else:
                time.sleep((desiredPairRefresh - dif) / 1000)
def websockThread(pair):
    pair
    pair.websocket = False
    pair.ob_Inst = GDaxBook(pair.ticker)
    time.sleep(2)
    pair.websocket = True
    while True:
        kill = 5 / pair.webSocketKill
        time.sleep(4)
def preparePairThread(pair):
    global prepared, overallNewData, stop_threads
    ticker = pair.ticker
    exc = pair.exchange
    cbn = exc + ticker
    while True:
        if (pair.prepare):
            prepared[cbn] = prepare_data(ticker, exc)
            overallNewData = True
            pair.Dataprepared = True
            # print(ticker, exc)
            os._exit(0)
            print("Exiting")
        while not pair.newData:
            time.sleep(1)
def handleArgs(argv):
    pass
def log(pLevel, pMessage):
    pass
def watchdog(a):
    global PAIRS
    global stop_threads
    time.sleep(0.1)  # get start
    print("Running...!")
    for pair in PAIRS:
        if refreshes == 1:
            stop_threads = True
        pair.threadWebsocket = threading.Thread(target=websockThread, args=(pair,))
        pair.threadWebsocket.daemon = False
        pair.threadWebsocket.start()
        time.sleep(0.2)
        pair.threadRecalc = threading.Thread(target=recalcThread, args=(pair,))
        pair.threadPrepare = threading.Thread(target=preparePairThread, args=(pair,))
        pair.threadRecalc.daemon = False
        pair.threadPrepare.daemon = False
        pair.threadRecalc.start()
        pair.threadPrepare.start()
def prepare_data(ticker, exchange):
    global stop_threads, okk
    secret_sell = []
    secret_buy = []
    combined = exchange + ticker
    data = get_data_cache(combined)
    pair.newData = False
    base_currency = ticker.split("-")[1]
    ob_ask = depth_ask[combined]
    ob_bid = depth_bid[combined]
    # Get Minimum and Maximum
    ladder_Bid_Min = fixNan(shape_bid[combined]['volume'].min())

    ladder_Bid_Max = fixNan(shape_bid[combined]['volume'].max(), False)
    ladder_Ask_Min = fixNan(shape_ask[combined]['volume'].min())
    ladder_Ask_Max = fixNan(shape_ask[combined]['volume'].max(), False)
    data_min = fixNan(data[TBL_VOLUME].min())
    data_max = fixNan(data[TBL_VOLUME].max(), False)
    ob_bid_max = fixNan(ob_bid[TBL_VOLUME].max(), False)
    ob_ask_max = fixNan(ob_ask[TBL_VOLUME].max(), False)

    symbol = SYMBOLS.get(base_currency.upper(), "")
    x_min = min([ladder_Bid_Min, ladder_Ask_Min, data_min])
    x_max = max([ladder_Bid_Max, ladder_Ask_Max, data_max, ob_ask_max, ob_bid_max])
    max_unique = max([fixNan(shape_bid[combined]['unique'].max(), False),
                      fixNan(shape_ask[combined]['unique'].max(), False)])
    width_factor = 15
    current_price = marketPrice[combined]
    if max_unique > 0: width_factor = 15 / max_unique

    market_price = marketPrice[combined]

    for index, row in shape_bid[combined].iterrows():

        cWidth = row['unique'] * width_factor
        vol = row[TBL_VOLUME]
        posY = (row['min_Price'] + row['max_Price']) / 2.0
        if cWidth > 15:
            cWidth = 15
        elif cWidth < 2:
            cWidth = 2

        tot_price1 = row["total_price"].replace(',', '')
        secret_sell.append("" + str(row['min_Price']) + "--" + str(row['max_Price']) + ": " + str(
            row['count']) + " buy orders * ₿" + str(row['unique']) + " = ₿" + str(row["volume"]) + " ($" + str(
            format_number(tot_price1)) + ")\n")

    for index, row in shape_ask[combined].iterrows():
        cWidth = row['unique'] * width_factor
        vol = row[TBL_VOLUME]
        posY = (row['min_Price'] + row['max_Price']) / 2.0

        if cWidth > 15:
            cWidth = 15
        elif cWidth < 2:
            cWidth = 2

        tot_price = row["total_price"].replace(',', '')
        secret_buy.append("" + str(row['min_Price']) + "--" + str(row['max_Price']) + ": " + str(
            row['count']) + " sell orders * ₿" + str(row['unique']) + " = ₿" + str(row["volume"]) + " ($" + str(
            format_number(tot_price)) + ")\n")

    #
    #                   ****Information about market****
    # **buy**#
    buy_number = float(ob_bid["text"][58][0:4])
    total_buy_order = str(ob_bid["text"][58])
    total_buy_order = "" + total_buy_order[16:20] + " buy orders valued at ₿" + total_buy_order[0:6] + ", " + \
                      total_buy_order[-1] + total_buy_order[-9:-1] + "--" + total_buy_order[-1] + str(
        current_price) + "\n"
    # **sell**#
    sell_number = (ob_ask['text'][58])
    sell_number = float(sell_number[0:4])
    total_sell_order = str(ob_ask['text'][58])
    total_sell_order = "" + total_sell_order[16:20] + " sell orders valued at ₿" + total_sell_order[0:6] + ", " + \
                       total_sell_order[-1] + str(current_price) + "--" + total_sell_order[-1] + total_sell_order[
                                                                                                 -9:-1] + "\n"
    print(total_sell_order)
    print(total_buy_order)

    if sell_number > buy_number:
        ccc = int(sell_number - buy_number)
        sheet_info = str(round(-abs(sell_number) + buy_number, 2))
        okk = "🔴Market may be oversold:Extra ₿" + str(ccc) + " will be sold.🔴\n"
    elif sell_number < buy_number:
        ccc = int(buy_number - sell_number)
        okk = "🟢Market may be overbought:Extra ₿" + str(ccc) + " will be bought.🟢\n"
        sheet_info = str(round(-abs(sell_number) + buy_number, 2))
    else:
        pass
    print(okk)

    uniqe_buy = []
    uniqe_sell = []
    for i in data["volume"].keys():
        if i > market_price:
            add_1 = "₿" + str(data["volume"][i]) + " one sell order at " + str(i) + " (" + format_number(
                str(float(data["volume"][i]) * market_price)) + ")\n"
            uniqe_buy.append(add_1)
        else:
            add_2 = "₿" + str(data["volume"][i]) + " one buy order at " + str(i) + " (" + format_number(
                str(float(data["volume"][i]) * market_price)) + ")\n"
            uniqe_sell.append(add_2)

    # # pprint(uniqe_buy)
    # # pprint(secret_buy)
    # # pprint(uniqe_sell)
    # # pprint(secret_sell)
    #
    # uniqe_buy = ''.join([str(elem) for elem in uniqe_buy])
    # secret_buy = ''.join([str(elem) for elem in secret_buy])
    # uniqe_sell = ''.join([str(elem) for elem in uniqe_sell])
    # secret_sell = ''.join([str(elem) for elem in secret_sell])
    # -----***Google Sheet***-----#

    sheet_acount = gspread.service_account(filename="YOUR.json")
    sheet_name = sheet_acount.open("YOUR SHEET")
    sheet_page = sheet_name.worksheet("NAME")
    sheet_page.update_acell("A1", sheet_info)

    # # -----****message Telegram****-----#
    # # message=total_sell_order+total_buy_order+okk+uniqe_buy+secret_buy+"------------\n"+uniqe_sell+secret_sell
    message=pair.ticker+"\n"+okk
    url = requests.get('https://api.telegram.org/bot000:'
                       "YOURID/sendMessage?chat_id="
                       '-YOUR&text='+message)
    from flask import Flask
    app = Flask(__name__)
    @app.route('/')
    def hello():
        return "<p>"+okk+"</p>"
    return app.run(host='0.0.0.0', port=8080)

if __name__ == '__main__':
    request = {"run_this": "BTC-USD"}
    watchdog(request)

