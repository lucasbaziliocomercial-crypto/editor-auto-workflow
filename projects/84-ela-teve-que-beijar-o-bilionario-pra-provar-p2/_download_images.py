import os, urllib.request, threading

BASE = r"C:\Users\Lucas Bazilio\AUTOMAÇÃO EDIÇÃO - WORKFLOW\projects\84-ela-teve-que-beijar-o-bilionario-pra-provar-p2\images"
os.makedirs(BASE, exist_ok=True)

URLS = {
  "img_001": "https://pikaso.cdnpk.net/private/production/4849476879/render.jpg?token=exp=1784073600~hmac=b14f8bc4f08d74212b53b2758ac26488a0c1c5344d9682c76364438f73ec69d6",
  "img_002": "https://pikaso.cdnpk.net/private/production/4849476806/render.jpg?token=exp=1784073600~hmac=644a0c0ad0ac8f07197a2774bcebd0e3750bdd5f76d57cf41b47a318ca12c932",
  "img_003": "https://pikaso.cdnpk.net/private/production/4849477201/render.jpg?token=exp=1784073600~hmac=6fef61622bddb01ff657fa39480c6cc15533e51874c704cf76eff9c94f101942",
  "img_004": "https://pikaso.cdnpk.net/private/production/4849477171/render.jpg?token=exp=1784073600~hmac=d190e5415ebfd472f8ee9efd637200799bb79544ccd21f1dcac02a3cf5e29a63",
  "img_005": "https://pikaso.cdnpk.net/private/production/4849477509/render.jpg?token=exp=1784073600~hmac=0e58d6a46a32152a4d91a84098bd8e5645707ba5c3f4eba0e503fa3596616bef",
  "img_006": "https://pikaso.cdnpk.net/private/production/4849478223/render.jpg?token=exp=1784073600~hmac=248785a7f10f1410fe8694f33313ad03f78dd8b2ca28a186ffd30deb4688d9d5",
  "img_007": "https://pikaso.cdnpk.net/private/production/4849478516/render.jpg?token=exp=1784073600~hmac=9993145bbd24779f2c278930b70b1f8b555241c69536e5e586703f5c0b05c38f",
  "img_008": "https://pikaso.cdnpk.net/private/production/4849479562/render.jpg?token=exp=1784073600~hmac=cd8523172b573aeb6f65101a107ecbcf529009fed2a4b73a7dca7dd4bcd5de36",
  "img_009": "https://pikaso.cdnpk.net/private/production/4849479560/render.jpg?token=exp=1784073600~hmac=634aa9d6d00b11ba70d9ee6c63979766cf37cb4ad47ab650b93497cb9c3deaa5",
  "img_010": "https://pikaso.cdnpk.net/private/production/4849479629/render.jpg?token=exp=1784073600~hmac=dda10827755f939481f2ebc17208fb550a55b8e273ff2a221c49b562c8c72756",
  "img_011": "https://pikaso.cdnpk.net/private/production/4849479823/render.jpg?token=exp=1784073600~hmac=1fae3ad0e6039b95f6acaae1d1faa25933b54421f00f8e07197d7b5eaca7d29e",
  "img_012": "https://pikaso.cdnpk.net/private/production/4849480292/render.jpg?token=exp=1784073600~hmac=f92f225992b22e4ce629f09a9f3dc8b30b001cc764ba49e2aecd999a6a4cf1bd",
  "img_013": "https://pikaso.cdnpk.net/private/production/4849480896/render.jpg?token=exp=1784073600~hmac=733289313602e3e46e162680d4aeeb322c032b67a57dfad4971a587a684951e5",
  "img_014": "https://pikaso.cdnpk.net/private/production/4849481555/render.jpg?token=exp=1784073600~hmac=2590d6066944c296d36f3806ab8b2c9a6da4850ab8b382de733d67c05452f83d",
  "img_015": "https://pikaso.cdnpk.net/private/production/4849482340/render.jpg?token=exp=1784073600~hmac=14e662cd493d1e406b7c5514b1714fc224640062cdd64463590e1f2ab13d4343",
  "img_016": "https://pikaso.cdnpk.net/private/production/4849482771/render.jpg?token=exp=1784073600~hmac=3095d6abd956690f6bf34167279d0f516e4429810a6a1002563ba473585e196a",
  "img_017": "https://pikaso.cdnpk.net/private/production/4849482488/render.jpg?token=exp=1784073600~hmac=2aabd0add74b21c36bde740b6c52f023aeb9f9400059660d6b533487088b0232",
  "img_018": "https://pikaso.cdnpk.net/private/production/4849483766/render.jpg?token=exp=1784073600~hmac=c6b1bbec1335dbdebf01026b9ac337f35fdfbfddafd8fa8887326edf55fc8961",
  "img_019": "https://pikaso.cdnpk.net/private/production/4849483309/render.jpg?token=exp=1784073600~hmac=238bd153acb4c5f5aae84638842c99e801c82ad80db230d565a6f9a0e997c8b9",
  "img_020": "https://pikaso.cdnpk.net/private/production/4849483716/render.jpg?token=exp=1784073600~hmac=9d36b57d92b90ba51084a6aac30ba8ded135d7219c4ecff78b86cd35cb2d8cfb",
  "img_021": "https://pikaso.cdnpk.net/private/production/4849484482/render.jpg?token=exp=1784073600~hmac=ef827c49caf9a58c1324aad8cc70353a82294e5f07cde1aa5dadbc702612e453",
  "img_022": "https://pikaso.cdnpk.net/private/production/4849484659/render.jpg?token=exp=1784073600~hmac=eff7bffd7a87d0e3a3830f0f1fcab20a5f2edb2e5a02f867bceebe93042ec976",
  "img_023": "https://pikaso.cdnpk.net/private/production/4849485230/render.jpg?token=exp=1784073600~hmac=bb62aaf08ded703b6dfceee4e591ed65509ac3878a997c3bd72bf9a48a141ea3",
  "img_024": "https://pikaso.cdnpk.net/private/production/4849485383/render.jpg?token=exp=1784073600~hmac=154bb090964daae7e485f9103442f73505504c842420413707a82b46fd1f2632",
  "img_025": "https://pikaso.cdnpk.net/private/production/4849485520/render.jpg?token=exp=1784073600~hmac=3ab0d19c46d1f4c002f688abfe7fcce2560f73af7b22b44091f94512e20a19bb",
  "img_026": "https://pikaso.cdnpk.net/private/production/4849486442/render.jpg?token=exp=1784073600~hmac=0c86b4a740834627c809779752bceed8fd52f2260e01960aa61850110305a424",
  "img_027": "https://pikaso.cdnpk.net/private/production/4849486580/render.jpg?token=exp=1784073600~hmac=798babd6656f90fcb20ba2b31b13f183e9d20e6468039e176c312a18923f35a7",
  "img_028": "https://pikaso.cdnpk.net/private/production/4849486518/render.jpg?token=exp=1784073600~hmac=bbad3986369c49a95dae383e4cbd0ee61a0d9419a777418aaf86fc066e45a0df",
  "img_029": "https://pikaso.cdnpk.net/private/production/4849488524/render.jpg?token=exp=1784073600~hmac=26026ef631108b336f60ae13f484cb96eb17cf123b3acce0db687adbeb99a5ee",
  "img_030": "https://pikaso.cdnpk.net/private/production/4849487830/render.jpg?token=exp=1784073600~hmac=c6bf95015d9fb9697996b3f4528c2df96214ba90ee72a8f99ccc6d1661be9ac2",
  "img_031": "https://pikaso.cdnpk.net/private/production/4849488224/render.jpg?token=exp=1784073600~hmac=3231ad8cc2ac5bec59293f800d8db38adb704071068580e3d9d9ba593850be65",
  "img_032": "https://pikaso.cdnpk.net/private/production/4849488315/render.jpg?token=exp=1784073600~hmac=b9432db7742867f64dc1a3ae394a3335112a1d22029eddcff027716c62320afe",
  "img_033": "https://pikaso.cdnpk.net/private/production/4849489079/render.jpg?token=exp=1784073600~hmac=18a97d65eed552eca858f5dcc1d51b305b5440641587bab48903397e73ae0171",
  "img_034": "https://pikaso.cdnpk.net/private/production/4849489469/render.jpg?token=exp=1784073600~hmac=20cc688afd4248e1c3029863b23bd2525be55b18357aaaa8b320a9df5efaff71",
  "img_035": "https://pikaso.cdnpk.net/private/production/4849489552/render.jpg?token=exp=1784073600~hmac=ac2e91e34d2c7894f89feea0aeaa33d30c5742cdcffc0591465980c73ff4f507",
  "img_036": "https://pikaso.cdnpk.net/private/production/4849489648/render.jpg?token=exp=1784073600~hmac=c734b62c129f82175a15a7821e95e72e3e52223db0c5b686b02af723fdc25626",
  "img_037": "https://pikaso.cdnpk.net/private/production/4849491121/render.jpg?token=exp=1784073600~hmac=f6ff08b7f5b16790ee490a96c33540ad290256b97ef2e65973c0ff390eabe086",
  "img_038": "https://pikaso.cdnpk.net/private/production/4849491991/render.jpg?token=exp=1784073600~hmac=092626db5ce92a60da11cd16229d5d2d4c391ca03d943c8ca820cb736f0bc23a",
  "img_039": "https://pikaso.cdnpk.net/private/production/4849491384/render.jpg?token=exp=1784073600~hmac=f99a295ef1beff64517dab9a4a2e1c30a8cac64c4209e034734a1987858b96be",
  "img_040": "https://pikaso.cdnpk.net/private/production/4849491442/render.jpg?token=exp=1784073600~hmac=7bab0d7d943983e15c5e7482fd8395cd6cc984ad49f55d484b0a7814132875bf",
  "img_041": "https://pikaso.cdnpk.net/private/production/4849491796/render.jpg?token=exp=1784073600~hmac=7e298928ca56d4141f67bec95189f92ab463b2474819795ab2259c17960ad602",
  "img_042": "https://pikaso.cdnpk.net/private/production/4849492531/render.jpg?token=exp=1784073600~hmac=212f6f4996a5f78f0ea6932ab325fa515bd8a049425057937b3aea177c8eab6f",
  "img_043": "https://pikaso.cdnpk.net/private/production/4849492691/render.jpg?token=exp=1784073600~hmac=91eb15e9367065a70a29d49a2867c55fce372d7d939318dbd410664c50848306",
  "img_044": "https://pikaso.cdnpk.net/private/production/4849493976/render.jpg?token=exp=1784073600~hmac=253228db0be35457d23d9665ec48b8d9a3a311051cb217d659ca5bb25b9af59a",
  "img_045": "https://pikaso.cdnpk.net/private/production/4849494690/render.jpg?token=exp=1784073600~hmac=d1bbccc2996bfe47ee96d5e7c47f5fc3badf55c9b351a8f6ff83ad00c5844da5",
  "img_046": "https://pikaso.cdnpk.net/private/production/4849494151/render.jpg?token=exp=1784073600~hmac=1e252549845c73f2cee2add8c721a1476c56734a91ce8c2f05ef116ce0cb7f0d",
  "img_047": "https://pikaso.cdnpk.net/private/production/4849494949/render.jpg?token=exp=1784073600~hmac=4c107c9c3357e003be8aa6e2bf2dde513c0fa7b9e2a04998c247bd1ab4123ef6",
  "img_048": "https://pikaso.cdnpk.net/private/production/4849494920/render.jpg?token=exp=1784073600~hmac=b0cf0b1c2433f71fb001fd73163fd4b2f880d3b0d12ef932dbd3455d7180f1be",
  "img_049": "https://pikaso.cdnpk.net/private/production/4849495769/render.jpg?token=exp=1784073600~hmac=04dc44f9eea24101e591ce8268b19f84852bd5941ad74e825b64d2f1699865ab",
}

errors = []
lock = threading.Lock()

def download(name, url):
    dest = os.path.join(BASE, name + ".png")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
    except Exception as e:
        with lock:
            errors.append(f"{name}: {e}")

threads = [threading.Thread(target=download, args=(k, v)) for k, v in URLS.items()]
for t in threads: t.start()
for t in threads: t.join()

saved = len([f for f in os.listdir(BASE) if f.endswith(".png")])
print(f"Salvos: {saved}/49")
if errors:
    print("Erros:", errors)
