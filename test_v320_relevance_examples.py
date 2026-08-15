from product_recognition import recognize_product

q='On Cloud 5'
exact=['On Cloud 5','On Running Cloud 5 Waterproof','On Cloud 5 Shoes Mens']
wrong=['ON Cloud 6 sneakers beige','ON Cloud X 4 AD training trainers in green','ON Cloud X 5 baskets blanches','On Cloudmonster 2']
for title in exact:
    r=recognize_product(q,title,marketplace='ASOS')
    assert r.accepted and r.level in {'fort','possible'}, (title,r)
for title in wrong:
    r=recognize_product(q,title,marketplace='ASOS')
    assert not r.accepted and r.level=='rejet', (title,r)
print('OK - V3.2.0 On Cloud 5 exact-model regression examples validated.')
