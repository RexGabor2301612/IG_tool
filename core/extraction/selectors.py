"""Platform-specific selectors for Instagram and Facebook."""

from enum import Enum
from dataclasses import dataclass


class Platform(Enum):
    """Supported platforms."""
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


@dataclass
class PlatformSelectors:
    """Container for platform-specific selectors."""
    
    # Login & auth
    login_form: str
    username_input: str
    password_input: str
    login_button: str
    
    # Verification/CAPTCHA
    captcha_challenge: str
    verification_challenge: str
    checkpoint_header: str
    
    # Post elements
    posts_container: str
    post_item: str
    post_url: str
    
    # Metrics
    likes_count: str
    comments_count: str
    shares_count: str
    post_timestamp: str
    
    # Navigation & readiness
    home_nav: str
    profile_icon: str
    main_content: str
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "login_form": self.login_form,
            "username_input": self.username_input,
            "password_input": self.password_input,
            "login_button": self.login_button,
            "captcha_challenge": self.captcha_challenge,
            "verification_challenge": self.verification_challenge,
            "checkpoint_header": self.checkpoint_header,
            "posts_container": self.posts_container,
            "post_item": self.post_item,
            "post_url": self.post_url,
            "likes_count": self.likes_count,
            "comments_count": self.comments_count,
            "shares_count": self.shares_count,
            "post_timestamp": self.post_timestamp,
            "home_nav": self.home_nav,
            "profile_icon": self.profile_icon,
            "main_content": self.main_content,
        }


class InstagramSelectors(PlatformSelectors):
    """Instagram-specific selectors."""
    
    def __init__(self):
        super().__init__(
            # Login & auth
            login_form='[aria-label="Log in"]',
            username_input='input[name="username"]',
            password_input='input[name="password"]',
            login_button='button:has-text("Log in")',
            
            # Verification/CAPTCHA
            captcha_challenge='[aria-label*="checkpoint"]',
            verification_challenge='[role="dialog"]',
            checkpoint_header='h2:has-text("Confirm")',
            
            # Post elements
            posts_container='main',
            post_item='article',
            post_url='a[href*="/p/"]',
            
            # Metrics
            likes_count='[aria-label*="like"]',
            comments_count='[aria-label*="comment"]',
            shares_count='[aria-label*="share"]',
            post_timestamp='time',
            
            # Navigation & readiness
            home_nav='[aria-label="Home"]',
            profile_icon='[aria-label*="profile"]',
            main_content='main > div',
        )


class FacebookSelectors(PlatformSelectors):
    """Facebook-specific selectors."""
    
    def __init__(self):
        super().__init__(
            # Login & auth
            login_form='form[aria-label="Log in"]',
            username_input='input[name="email"]',
            password_input='input[name="pass"]',
            login_button='button[name="login"]',
            
            # Verification/CAPTCHA
            captcha_challenge='[data-testid="captcha"]',
            verification_challenge='[role="dialog"]',
            checkpoint_header='h2:has-text("Confirm")',
            
            # Post elements
            posts_container='div[role="main"]',
            post_item='div[data-testid="post"]',
            post_url='a[href*="/posts/"]',
            
            # Metrics
            likes_count='span:has-text("people")',
            comments_count='span:has-text("comments")',
            shares_count='span:has-text("shares")',
            post_timestamp='span[data-utime]',
            
            # Navigation & readiness
            home_nav='a[aria-label="Home"]',
            profile_icon='a[aria-label*="profile"]',
            main_content='div[role="main"]',
        )


class SelectorFactory:
    """Factory for creating platform-specific selectors."""
    
    _SELECTORS = {
        Platform.INSTAGRAM: InstagramSelectors,
        Platform.FACEBOOK: FacebookSelectors,
    }
    
    @classmethod
    def get_selectors(cls, platform: Platform) -> PlatformSelectors:
        """Get selectors for platform.
        
        Args:
            platform: Platform enum
            
        Returns:
            PlatformSelectors instance
        """
        selector_class = cls._SELECTORS.get(platform)
        if not selector_class:
            raise ValueError(f"Unknown platform: {platform}")
        return selector_class()
    
    @classmethod
    def get_by_name(cls, platform_name: str) -> PlatformSelectors:
        """Get selectors by platform name.
        
        Args:
            platform_name: "instagram" or "facebook"
            
        Returns:
            PlatformSelectors instance
        """
        try:
            platform = Platform(platform_name.lower())
            return cls.get_selectors(platform)
        except ValueError:
            raise ValueError(f"Unknown platform: {platform_name}")
