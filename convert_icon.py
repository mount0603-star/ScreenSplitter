from PIL import Image
try:
    img = Image.open("app.png")
    # Resize to standard icon sizes if needed, or just save
    # High quality resize
    img.save("app.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("Icon converted successfully: app.ico")
except Exception as e:
    print(f"Error converting icon: {e}")
