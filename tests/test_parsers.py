"""Parser tests on trimmed HTML shaped like the live pages (Oct 2026)."""

from bearbrick_scout.models import ListingType, Source
from bearbrick_scout.sources import buyee, ebay, mercari_us

YAHOO_SEARCH = """
<ul><li class="itemCard"><div class="itemCard__item">
  <div class="g-thumbnail__outer"><a href="/item/jdirectitems/auction/q1247940605?conversionType=x">
    <img class="g-thumbnail__image" alt="現状品 XLARGE ベアブリック フィギュア 400%サイ" src="https://cdnyauction-pctr.buyee.jp/i/a.jpg"></a></div>
  <div class="itemCard__itemInfo">
    <div class="itemCard__itemName"><a href="/item/jdirectitems/auction/q1247940605">As-is XLARGE Bearbrick figure, 400% size</a></div>
    <ul class="g-priceDetails__list"><li class="g-priceDetails__item"><span class="g-title">Current Price</span>
      <div class="g-price__outer"><span class="g-price">5,780 YEN</span><span class="g-priceFx">(US$38.27)</span></div></li></ul>
    <ul class="itemCard__infoList">
      <li class="itemCard__infoItem"><span class="g-title">Time Remaining</span><span class="g-text">5 hour(s)</span></li>
      <li class="itemCard__infoItem"><span class="g-title">Number of Bids</span><span class="g-text">5</span></li>
    </ul></div></div></li></ul>
"""

MERCARI_JP_SEARCH = """
<ul class="item-lists"><li class="list">
  <a href="/mercari/item/m32604776654?conversionType=Mercari_DirectSearch">
    <div class="thumbnail-area"><img class="thumbnail" alt="BE@RBRICK RAT FINK 100％ &amp; 400％" src="//static.mercdn.net/thumb/item/jpeg/m32604776654_1.jpg?1"></div>
    <h2 class="name">BE@RBRICK RAT FINK 100% &amp; 400%</h2>
    <p class="price">29,800 YEN</p><p class="price-fx">(US$197.28)</p></a></li></ul>
"""

MERCARI_US_SEARCH = """
<a data-testid="ProductThumbWrapper" href="/us/item/m46624947335/?ref=search_results">
  <div data-testid="ItemContainer" data-productid="m46624947335" data-is-on-sale="true">
    <img alt="Javier calleja 400% No box - Bearbrick" title="Bearbrick Javier calleja 400% No box"
      srcset="https://u-mercari-images.mercdn.net/photos/m46624947335_1.jpg?width=32 32w">
    <div data-testid="ItemName">Bearbrick Javier calleja 400% No box</div>
    <div data-testid="ProductThumbItemPrice">$95.00</div></div></a>
<a data-testid="ProductThumbWrapper" href="/us/item/m1/"><div data-testid="ItemContainer" data-productid="m1" data-is-on-sale="false">
  <div data-testid="ItemName">Sold one 400%</div><div data-testid="ProductThumbItemPrice">$5.00</div></div></a>
"""


def test_yahoo_search():
    [it] = buyee.parse_yahoo_search(YAHOO_SEARCH)
    assert it.source is Source.BUYEE_YAHOO and it.source_id == "q1247940605"
    assert it.price_usd == 38.27 and it.ships_from == "JP"
    assert it.listing_type is ListingType.AUCTION and it.bid_count == 5
    assert it.title_original.startswith("現状品")
    assert it.url == "https://buyee.jp/item/jdirectitems/auction/q1247940605"


def test_mercari_jp_search():
    [it] = buyee.parse_mercari_search(MERCARI_JP_SEARCH)
    assert it.source_id == "m32604776654" and it.price_usd == 197.28
    assert it.listing_type is ListingType.BUY_IT_NOW
    assert it.image_urls == ["https://static.mercdn.net/thumb/item/jpeg/m32604776654_1.jpg?1"]


def test_buyee_detail_pages():
    yahoo = buyee.parse_yahoo_detail(
        '<ul><li><span class="g-title">Item Condition</span><span class="g-text">A little damaged/dirty</span></li></ul>'
        '<img src="https://cdnyauction.buyee.jp/image/dr000/a/i-img1200x900-1.jpg">'
        '<img src="https://cdnyauction.buyee.jp/image/dr000/a/i-img1200x900-1.jpg">'
        '<iframe src="/item/jdirectitems/auction/q1/detail#googtrans(ja|)"></iframe>'
    )
    assert yahoo["condition"] == "A little damaged/dirty"
    assert yahoo["image_urls"] == ["https://cdnyauction.buyee.jp/image/dr000/a/i-img1200x900-1.jpg"]
    assert yahoo["description_url"] == "https://buyee.jp/item/jdirectitems/auction/q1/detail"

    mer = buyee.parse_mercari_detail(
        '<a class="js-smartPhoto" href="https://static.mercdn.net/item/detail/orig/photos/m3_1.jpg?17"></a>'
        '<a class="js-smartPhoto" href="https://static.mercdn.net/item/detail/orig/photos/m3_2.jpg?17"></a>'
        '<iframe src="/mercari/item/description/m3#googtrans(ja|en)"></iframe>'
    )
    assert len(mer["image_urls"]) == 2
    assert mer["description_url"] == "https://buyee.jp/mercari/item/description/m3"


def test_mercari_us_search_skips_sold():
    [it] = mercari_us.parse_search(MERCARI_US_SEARCH)
    assert it.source_id == "m46624947335" and it.price_usd == 95.0 and it.ships_from == "US"
    assert it.image_urls == ["https://u-mercari-images.mercdn.net/photos/m46624947335_1.jpg"]


def test_mercari_us_item_shipping():
    assert mercari_us.parse_item("<body>Shipping $8.99 USPS</body>", "m1")["shipping_usd"] == 8.99
    assert mercari_us.parse_item("<body>Free shipping</body>", "m1")["shipping_usd"] == 0.0
    assert mercari_us.parse_item("<body>Nothing here</body>", "m1")["shipping_usd"] is None


def test_ebay_mapping():
    item = {
        "itemId": "v1|123|0", "title": "Bearbrick 400% Snoopy", "itemWebUrl": "https://ebay.com/itm/123",
        "price": {"value": "110.00", "currency": "USD"},
        "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "9.50", "currency": "USD"}}],
        "itemLocation": {"country": "US"}, "condition": "New",
        "image": {"imageUrl": "a.jpg"}, "additionalImages": [{"imageUrl": "b.jpg"}],
    }
    it = ebay.to_listing(item)
    assert it.best_offer and it.listing_type is ListingType.BUY_IT_NOW
    assert (it.price_usd, it.shipping_usd, it.ships_from) == (110.0, 9.5, "US")
    assert it.image_urls == ["a.jpg", "b.jpg"]

    auction = ebay.to_listing({**item, "buyingOptions": ["AUCTION"], "currentBidPrice": {"value": "41.00"},
                               "shippingOptions": [{"shippingCostType": "CALCULATED"}]})
    assert auction.listing_type is ListingType.AUCTION and auction.price_usd == 41.0
    assert auction.shipping_usd is None


def test_buyee_condition_layouts():
    yahoo = '<li class="itemDetail__list"><div class="itemDetail__listName">Item Condition</div>' \
            '<div class="itemDetail__listValue">A little damaged/dirty</div></li>'
    assert buyee.parse_yahoo_detail(yahoo)["condition"] == "A little damaged/dirty"
    mer = '<dl class="m-goodsTable"><dt>Brand</dt><dd>x</dd><dt>Item Condition</dt><dd><a>New/Unused</a></dd></dl>'
    assert buyee.parse_mercari_detail(mer)["condition"] == "New/Unused"


def test_truncated_original_title_dropped():
    assert buyee._distinct_original("BE@RBRICK 100%", "BE@RBRICK 100% 400% SPIDER-MAN") is None
    assert buyee._distinct_original("ベアブリック 400%", "Bearbrick 400%") == "ベアブリック 400%"
