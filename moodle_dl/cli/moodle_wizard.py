import sys
from getpass import getpass

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.utils import Log


class MoodleWizard:
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        self.config = config
        self.opts = opts

    def interactively_acquire_token(self, use_stored_url: bool = False) -> str:
        if self.opts.sso or self.opts.token is not None:
            self.interactively_acquire_sso_token(use_stored_url=use_stored_url)
        else:
            self.interactively_acquire_normal_token(use_stored_url=use_stored_url)

    def interactively_get_moodle_url(self, use_stored_url: bool) -> MoodleURL:
        if use_stored_url:
            return self.config.get_moodle_URL()

        url_ok = False
        while not url_ok:
            url_ok = True
            moodle_url = input('Moodle 的 URL:   ')

            use_http = False
            if moodle_url.startswith('http://'):
                Log.warning(
                    '警告：你输入了不安全的 URL！你确定该 Moodle 无法通过 `https://` 访问吗？'
                    + '你的所有数据将以不安全的方式传输！如果你的 Moodle 可以通过 `https://` 访问，'
                    + '请使用 `https://` 重新运行该过程以保护你的数据。'
                )
                use_http = True
            elif not moodle_url.startswith('https://'):
                Log.error('你的 Moodle URL 必须以 `https://` 开头')
                url_ok = False

        moodle_domain, moodle_path = MoodleService.split_moodle_url(moodle_url)
        return MoodleURL(use_http, moodle_domain, moodle_path)

    def interactively_acquire_normal_token(self, use_stored_url: bool = False) -> str:
        """
        Walks the user through executing a login into the Moodle-System to get
        the Token and saves it.
        @return: The Token for Moodle.
        """

        automated = False
        automatic_run_once = False
        if self.opts.username is not None and self.opts.password is not None:
            automated = True

        if not automated:
            print('[以下凭据不会被保存，仅临时用于生成登录令牌。]')

        moodle_token = None
        while moodle_token is None and not automatic_run_once:
            moodle_url = self.interactively_get_moodle_url(use_stored_url)

            if automated:
                automatic_run_once = True

            if self.opts.username is not None:
                moodle_username = self.opts.username
            else:
                moodle_username = input('Moodle 用户名:   ')

            if self.opts.password is not None:
                moodle_password = self.opts.password
            else:
                moodle_password = getpass('Moodle 密码 [无输出显示]:   ')

            try:
                moodle_token, moodle_privatetoken = MoodleService(self.config, self.opts).obtain_login_token(
                    moodle_username, moodle_password, moodle_url
                )

            except RequestRejectedError as error:
                Log.error(f'登录失败！({error}) 请重试。')
            except (ValueError, RuntimeError) as error:
                Log.error(f'与 Moodle 系统通信时出错！({error}) 请重试。')
            except ConnectionError as error:
                Log.error(str(error))

        if automated is True and moodle_token is None:
            sys.exit(1)

        self.config.set_tokens(moodle_token, moodle_privatetoken)
        self.config.set_moodle_URL(moodle_url)

        Log.success('令牌已成功保存！')

        return moodle_token

    def interactively_acquire_sso_token(self, use_stored_url: bool = False) -> str:
        """
        Walks the user through the receiving of a SSO token
        @return: The Token for Moodle.
        """

        moodle_url = self.interactively_get_moodle_url(use_stored_url)

        if self.opts.token is not None:
            moodle_token = self.opts.token
        else:
            Log.warning('请使用 Chrome 浏览器进行以下操作')
            print('1. 登录你的 Moodle 账户')
            print('2. 打开开发者控制台（按 F12）并转到 Network（网络）标签')
            print('3. 然后在你已登录的同一浏览器标签页中访问以下 URL：')

            print(
                moodle_url.url_base
                + 'admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl'
            )
            print()
            print(
                '如果你打开链接，不应该加载网页，而是会出现错误。'
                + '在你之前打开的开发者控制台的 Network 标签中应该有一个错误条目。'
            )

            print('脚本期望一个类似这样的 URL：')
            Log.info('moodledl://token=$apptoken')
            print(
                ' 其中 $apptoken 看起来是随机的，"moodledl" 也可以是不同的 URL scheme，'
                + '比如 "moodlemobile"。实际上 $apptoken 是一个包含访问 Moodle 令牌的 Base64 字符串。'
            )

            print(
                '4. 复制无法加载的网站的链接地址'
                + '（右键单击列表条目，然后单击"复制"，然后单击"复制链接地址"）'
            )

            token_address = input('然后在此处插入链接地址:   ')

            moodle_token, moodle_privatetoken = MoodleService.extract_token(token_address)
            if moodle_token is None:
                raise ValueError('无效的 URL！')

        self.config.set_tokens(moodle_token, moodle_privatetoken)
        self.config.set_moodle_URL(moodle_url)

        Log.success('令牌已成功保存！')

        return moodle_token
