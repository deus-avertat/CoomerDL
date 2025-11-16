![Windows Compatibility](https://img.shields.io/badge/Windows-10%2C%2011-blue)
![Downloads](https://img.shields.io/github/downloads/emy69/CoomerDL/total)

# Coomer Downloader App

**Coomer Downloader App** is a Python-based desktop application that simplifies downloading images and videos from various URLs. With an intuitive GUI, you can paste a link and let the app handle the rest.

---

## Support My Work

If you find this tool helpful, please consider supporting my efforts:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00.svg?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/emy_69)
[![Support on Patreon](https://img.shields.io/badge/Support%20on%20Patreon-FF424D.svg?style=for-the-badge&logo=patreon&logoColor=white)](https://www.patreon.com/emy69)


---

## Features

### Download Images and Videos
- **Multithreaded Downloads**: Boosts download speed by utilizing multiple threads.
- **Progress Feedback**: Real-time progress updates during downloads.
- **Queue Management**: Efficiently handles large download queues.
- **Fail-fast Retries**: Downloads retry up to five times (six total attempts) by default before failing, and the limit can be adjusted in the Settings panel.

**Supported File Extensions**:
- **Videos**: `.mp4`, `.mkv`, `.webm`, `.mov`, `.avi`, `.flv`, `.wmv`, `.m4v`
- **Images**: `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`
- **Documents**: `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`
- **Compressed**: `.zip`, `.rar`, `.7z`, `.tar`, `.gz`

---

## Supported Pages

- [coomer.su](https://coomer.su/)  
- [kemono.su](https://kemono.su/)  
- [erome.com](https://www.erome.com/)  
- [bunkr.albums.io](https://bunkr-albums.io/)  
- [simpcity.su](https://simpcity.su/)  
- [jpg5.su](https://jpg5.su/)  

---

## CLI Tools

If you prefer using command-line interfaces, check out the following projects:

- **[Coomer CLI](https://github.com/Emy69/Coomer-cli)**  
  A CLI tool for downloading media from Coomer and similar sites. It offers customizable options for file naming, download modes, rate limiting, checksum verification, and more.

- **[Simpcity CLI](https://github.com/Emy69/SimpCityCLI)**  
  A CLI tool specifically designed for downloading media from Simpcity. It shares many features with Coomer CLI and is tailored for the Simpcity platform.

---


## Language Support

- [Español](#)  
- [English](#)  
- [日本語 (Japanese)](#)  
- [中文 (Chinese)](#)  
- [Français (French)](#)  
- [Русский (Russian)](#)  

---

## Community

Have questions or just want to say hi? Join the Discord server:

[![Join Discord](https://img.shields.io/badge/Join-Discord-7289DA.svg?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/ku8gSPsesh)

---

## Downloads

- **Latest Version**: Visit the [Releases Page](https://github.com/Emy69/CoomerDL/releases) to download the newest version.

---

## Usage
**Standard Download**
1. Launch the application.
2. Paste the URL of the image or video you want to download.
3. Click **Download** and wait for the process to finish.

**Download with Preflight Check**
1. Launch the application.
2. Paste the URL of the image or video you want to download.
3. Tick **Preflight Post Selection**.
4. In the window that pops up, select the posts you want or use the filters to select posts matching certain criteria.

<img width="1885" height="833" alt="image" src="https://github.com/user-attachments/assets/1974c23a-4a80-44bd-ae45-90b1d1ff9650" />


### SimpCity cookies and privacy

- SimpCity logins rely on cookies stored in `resources/config/simpcity_cookies.enc`. The file is encrypted with a password you provide; the password is never written to disk.
- The application prompts for the password whenever encrypted cookies are read or saved. Advanced users can skip the dialog by setting the `COOMERDL_COOKIES_PASSWORD` environment variable before launching the app.
- Cookie storage can be toggled and wiped from **Settings → General → Privacy & Cookies**. Use the **Delete encrypted cookies** button to remove the file instantly if you no longer need it.

---

## Clone the Repository

To get a local copy of the project, run the following command:

```sh
git clone https://github.com/Emy69/CoomerDL.git
```
### Install Dependencies
Navigate to the project folder:
```sh
cd CoomerDL
```
Then install the required dependencies:
```sh
pip install -r requirements.txt
```
### Run the Application
Once everything is installed, you can start the application with:
```sh
python main.py
```
