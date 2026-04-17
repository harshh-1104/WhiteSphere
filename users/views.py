from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from .forms import UserRegisterForm, UserLoginForm, UserUpdateForm
from .models import User, EmailOTP
from interactions.models import Follow, Like


def register_view(request):
    """Step 1: Collect user info and send OTP to email."""
    if request.user.is_authenticated:
        return redirect('posts:home')

    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            # Store form data in session (don't create user yet)
            request.session['reg_username'] = form.cleaned_data['username']
            request.session['reg_email'] = form.cleaned_data['email']
            request.session['reg_password'] = form.cleaned_data['password1']

            # Generate and send OTP
            email = form.cleaned_data['email']
            otp_code = EmailOTP.generate_otp()

            # Delete old OTPs for this email
            EmailOTP.objects.filter(email=email).delete()

            # Save new OTP
            EmailOTP.objects.create(email=email, otp=otp_code)

            # Send email
            try:
                html_message = render_to_string('users/email_otp.html', {'otp_code': otp_code})
                send_mail(
                    subject='WriteSphere — Your Verification Code',
                    message=f'Your OTP verification code is: {otp_code}\n\n'
                            f'This code expires in 5 minutes.\n\n'
                            f'If you did not request this, please ignore this email.',
                    from_email=settings.EMAIL_HOST_USER,
                    recipient_list=[email],
                    html_message=html_message,
                    fail_silently=False,
                )
                messages.info(request, f'A 6-digit OTP has been sent to {email}')
            except Exception as e:
                messages.error(request, f'Failed to send OTP. Please try again. Error: {e}')
                return render(request, 'users/register.html', {'form': form})

            return redirect('users:verify_otp')
    else:
        form = UserRegisterForm()

    return render(request, 'users/register.html', {'form': form})


def verify_otp_view(request):
    """Step 2: Verify OTP and create the user account."""
    # Check if registration data exists in session
    email = request.session.get('reg_email')
    if not email:
        messages.error(request, 'Please complete the registration form first.')
        return redirect('users:register')

    if request.method == 'POST':
        entered_otp = request.POST.get('otp', '').strip()

        if not entered_otp:
            messages.error(request, 'Please enter the OTP code.')
            return render(request, 'users/verify_otp.html', {'email': email})

        # Find matching OTP
        try:
            otp_obj = EmailOTP.objects.filter(
                email=email,
                otp=entered_otp,
                is_verified=False,
            ).latest('created_at')
        except EmailOTP.DoesNotExist:
            messages.error(request, 'Invalid OTP code. Please try again.')
            return render(request, 'users/verify_otp.html', {'email': email})

        # Check expiry
        if otp_obj.is_expired:
            messages.error(request, 'OTP has expired. Please request a new one.')
            return render(request, 'users/verify_otp.html', {'email': email})

        # OTP is valid — create the user
        otp_obj.is_verified = True
        otp_obj.save()

        username = request.session.get('reg_username')
        password = request.session.get('reg_password')

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=User.Role.READER,
        )

        # Clean session
        for key in ['reg_username', 'reg_email', 'reg_password']:
            request.session.pop(key, None)

        login(request, user)
        messages.success(request, f'Welcome to WriteSphere, {user.username}! Your email has been verified.')
        return redirect('posts:home')

    return render(request, 'users/verify_otp.html', {'email': email})


def resend_otp_view(request):
    """Resend OTP to the email in session."""
    email = request.session.get('reg_email')
    if not email:
        return redirect('users:register')

    # Generate new OTP
    otp_code = EmailOTP.generate_otp()
    EmailOTP.objects.filter(email=email).delete()
    EmailOTP.objects.create(email=email, otp=otp_code)

    try:
        html_message = render_to_string('users/email_otp.html', {'otp_code': otp_code})
        send_mail(
            subject='WriteSphere — Your New Verification Code',
            message=f'Your new OTP verification code is: {otp_code}\n\n'
                    f'This code expires in 5 minutes.',
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=[email],
            html_message=html_message,
            fail_silently=False,
        )
        messages.success(request, f'A new OTP has been sent to {email}')
    except Exception:
        messages.error(request, 'Failed to resend OTP. Please try again.')

    return redirect('users:verify_otp')


def login_view(request):
    """Handle user login."""
    if request.user.is_authenticated:
        return redirect('posts:home')

    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'Welcome back, {user.username}!')
            next_url = request.GET.get('next', 'posts:home')
            return redirect(next_url)
    else:
        form = UserLoginForm()

    return render(request, 'users/login.html', {'form': form})


@login_required
def logout_view(request):
    """Handle user logout."""
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('posts:home')


def profile_view(request, username):
    """View a user's profile and their posts."""
    profile_user = get_object_or_404(User, username=username)
    posts = profile_user.posts.filter(status='published')

    is_following = False
    if request.user.is_authenticated and request.user != profile_user:
        is_following = Follow.objects.filter(
            follower=request.user,
            following=profile_user,
        ).exists()

    liked_post_ids = set()
    if request.user.is_authenticated:
        liked_post_ids = set(
            Like.objects.filter(user=request.user, post__in=posts)
            .values_list('post_id', flat=True)
        )

    # Replies: comments made by this user on any post
    from interactions.models import Comment
    user_comments = Comment.objects.filter(
        author=profile_user
    ).select_related('post', 'post__author').order_by('-created_at')

    # Likes: posts this user has liked
    user_liked_posts = Like.objects.filter(
        user=profile_user
    ).select_related('post', 'post__author').order_by('-created_at')

    # Followers & Following lists
    followers_list = Follow.objects.filter(
        following=profile_user
    ).select_related('follower')
    following_list = Follow.objects.filter(
        follower=profile_user
    ).select_related('following')

    # Which tab is active? Default to 'posts'
    active_tab = request.GET.get('tab', 'posts')

    context = {
        'profile_user': profile_user,
        'posts': posts,
        'is_following': is_following,
        'liked_post_ids': liked_post_ids,
        'user_comments': user_comments,
        'user_liked_posts': user_liked_posts,
        'followers_list': followers_list,
        'following_list': following_list,
        'active_tab': active_tab,
    }
    return render(request, 'users/profile.html', context)


@login_required
def profile_edit_view(request):
    """Edit the logged-in user's profile."""
    if request.method == 'POST':
        form = UserUpdateForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('users:profile', username=request.user.username)
    else:
        form = UserUpdateForm(instance=request.user)

    return render(request, 'users/profile_edit.html', {'form': form})

